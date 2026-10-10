#!/usr/bin/env python3
"""Free guide bot for @cantonfair_china_bot.

Uses only the Python standard library. Keep the Telegram token in environment
variables; never paste it into this file.
"""

import json
import hashlib
import hmac
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
VIDEO_PATH = Path(os.environ.get("GUIDE_VIDEO_PATH", "assets/guide-video.mov"))
PDF_PATH = Path(os.environ.get("GUIDE_PDF_PATH", "assets/guide.pdf"))
DATA_PATH = Path(os.environ.get("DATA_PATH", "data/bot.sqlite3"))
API_ROOT = f"https://api.telegram.org/bot{TOKEN}"

# Instagram / Meta configuration. Keep all secrets in Railway variables.
INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
INSTAGRAM_PROFILE_URL = os.environ.get("INSTAGRAM_PROFILE_URL", "")
INSTAGRAM_APP_SECRET = os.environ.get("INSTAGRAM_APP_SECRET", "")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "")
INSTAGRAM_API_VERSION = os.environ.get("INSTAGRAM_API_VERSION", "v24.0")
TELEGRAM_BOT_LINK = os.environ.get(
    "TELEGRAM_BOT_LINK", "https://t.me/cantonfair_china_bot?start=instagram"
)
REQUIRED_TELEGRAM_CHANNEL = os.environ.get("REQUIRED_TELEGRAM_CHANNEL", "@r_zaynullin")
REQUIRED_TELEGRAM_CHANNEL_LINK = os.environ.get(
    "REQUIRED_TELEGRAM_CHANNEL_LINK", "https://t.me/r_zaynullin"
)
INSTAGRAM_API_ROOT = f"https://graph.instagram.com/{INSTAGRAM_API_VERSION}"

WELCOME = (
    "Привет! 👋\n\n"
    "Здесь вы получите бесплатный пошаговый гайд: «Как найти фабрики и их "
    "продукцию с Кантонской выставки в Китае 2026 года».\n\n"
    "Нажмите кнопку — и я пришлю видео и PDF-инструкцию."
)

INSTAGRAM_KEYWORDS = {"гайд", "guide", "start"}
INSTAGRAM_READY_WORDS = {"готово", "готов", "ready"}
RECENT_EVENT_IDS = set()
RECENT_EVENT_LOCK = threading.Lock()


def api(method: str, payload=None, files=None):
    """Call Telegram Bot API, supporting both JSON and multipart uploads."""
    url = f"{API_ROOT}/{method}"
    payload = payload or {}
    try:
        if files:
            boundary = f"----GuideBot{uuid.uuid4().hex}"
            body = bytearray()

            def part(name, value):
                body.extend(f"--{boundary}\r\n".encode())
                body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
                body.extend(str(value).encode())
                body.extend(b"\r\n")

            for name, value in payload.items():
                part(name, value)
            for field, path in files.items():
                body.extend(f"--{boundary}\r\n".encode())
                body.extend(
                    f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'.encode()
                )
                body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
                body.extend(path.read_bytes())
                body.extend(b"\r\n")
            body.extend(f"--{boundary}--\r\n".encode())
            request = urllib.request.Request(
                url,
                data=bytes(body),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        else:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode())
    except urllib.error.URLError as error:
        raise RuntimeError(f"Telegram API unavailable: {error}") from error
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result["result"]


def instagram_api(path: str, payload: dict):
    """Call the Instagram API without ever logging its access token."""
    if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        raise RuntimeError("Instagram API is not configured.")
    request = urllib.request.Request(
        f"{INSTAGRAM_API_ROOT}/{path.lstrip('/')}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {INSTAGRAM_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"Instagram API error {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Instagram API unavailable: {error}") from error


def send_instagram_text(recipient_id: str, text: str, quick_replies=None):
    message = {"text": text}
    if quick_replies:
        message["quick_replies"] = quick_replies
    return instagram_api(
        f"{INSTAGRAM_ACCOUNT_ID}/messages",
        {"recipient": {"id": str(recipient_id)}, "message": message},
    )


def send_instagram_button(recipient_id: str, text: str, button: dict):
    """Send a Meta button template; fallback is handled by the caller."""
    return instagram_api(
        f"{INSTAGRAM_ACCOUNT_ID}/messages",
        {
            "recipient": {"id": str(recipient_id)},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {"template_type": "button", "text": text, "buttons": [button]},
                }
            },
        },
    )


def instagram_subscription_prompt(recipient_id: str):
    text = (
        "Чтобы получить гайд, сначала подпишитесь на наш Instagram. "
        "После подписки вернитесь сюда и напишите «готово»."
    )
    if INSTAGRAM_PROFILE_URL:
        try:
            return send_instagram_button(
                recipient_id,
                text,
                {
                    "type": "web_url",
                    "url": INSTAGRAM_PROFILE_URL,
                    "title": "Подписаться на Instagram",
                },
            )
        except RuntimeError as error:
            print(f"Instagram button failed, using a link: {error}", file=sys.stderr)
            text = f"{text}\n\n{INSTAGRAM_PROFILE_URL}"
    return send_instagram_text(recipient_id, text)


def instagram_confirmation_prompt(recipient_id: str):
    text = "Если вы уже подписались, подтвердите это кнопкой ниже."
    try:
        return send_instagram_button(
            recipient_id,
            text,
            {"type": "postback", "title": "Готово, подписался", "payload": "confirm_subscription"},
        )
    except RuntimeError as error:
        print(f"Instagram confirmation button failed: {error}", file=sys.stderr)
        return send_instagram_text(recipient_id, "Напишите «подтверждаю», чтобы получить ссылку.")


def send_telegram_link(recipient_id: str):
    return send_instagram_text(
        recipient_id,
        f"Спасибо! Вот ссылка на Telegram-бота с гайдом:\n{TELEGRAM_BOT_LINK}",
    )


def reply_to_instagram_comment(comment_id: str, text: str):
    return instagram_api(f"{comment_id}/replies", {"message": text})


def private_reply_to_instagram_comment(comment_id: str, text: str):
    return instagram_api(f"{comment_id}/private_replies", {"message": text})


def remember_event(event_id: str) -> bool:
    """Return False for a duplicate webhook event.

    Meta can retry a webhook delivery, so duplicate protection prevents sending
    the same public reply or Telegram link twice while the process is running.
    """
    if not event_id:
        return True
    with RECENT_EVENT_LOCK:
        if event_id in RECENT_EVENT_IDS:
            return False
        RECENT_EVENT_IDS.add(event_id)
        if len(RECENT_EVENT_IDS) > 2000:
            RECENT_EVENT_IDS.clear()
    return True


def handle_instagram_message(event: dict):
    sender = (event.get("sender") or {}).get("id")
    if not sender:
        return
    message = event.get("message") or {}
    postback = event.get("postback") or {}
    text = (message.get("text") or "").strip().lower()
    payload = (postback.get("payload") or "").strip()
    event_id = message.get("mid") or postback.get("mid") or f"{sender}:{text}:{payload}"
    if not remember_event(event_id):
        return

    if payload == "confirm_subscription" or text in {"подтверждаю", "confirm"}:
        send_telegram_link(sender)
    elif text in INSTAGRAM_READY_WORDS:
        instagram_confirmation_prompt(sender)
    elif text in INSTAGRAM_KEYWORDS:
        instagram_subscription_prompt(sender)
    else:
        send_instagram_text(
            sender,
            "Напишите «гайд», чтобы получить инструкцию по Кантонской выставке.",
        )


def handle_instagram_comment(change: dict):
    value = change.get("value") or {}
    comment_id = str(value.get("id") or "")
    text = (value.get("text") or "").strip().lower()
    if not comment_id or not remember_event(comment_id):
        return
    if text not in INSTAGRAM_KEYWORDS:
        return

    # A public reply makes the next action clear. Meta permits a single private
    # reply to an eligible comment, which starts the Direct conversation.
    reply_to_instagram_comment(comment_id, "Проверьте Direct ✉️")
    try:
        private_reply_to_instagram_comment(
            comment_id,
            "Чтобы получить гайд, подпишитесь на наш Instagram. "
            "После подписки вернитесь в Direct и напишите «готово»."
            + (f"\n\n{INSTAGRAM_PROFILE_URL}" if INSTAGRAM_PROFILE_URL else ""),
        )
    except RuntimeError as error:
        # Keep the public comment reply even if the private reply cannot be
        # delivered (for example, when Meta's time window does not allow it).
        print(f"Instagram private comment reply failed: {error}", file=sys.stderr)


def process_instagram_webhook(payload: dict):
    for entry in payload.get("entry", []):
        for message in entry.get("messaging", []):
            try:
                handle_instagram_message(message)
            except Exception as error:
                print(f"Instagram message handling error: {error}", file=sys.stderr)
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue
            try:
                handle_instagram_comment(change)
            except Exception as error:
                print(f"Instagram comment handling error: {error}", file=sys.stderr)


class MetaWebhookHandler(BaseHTTPRequestHandler):
    """HTTPS is provided by Railway; this server receives proxied requests."""

    server_version = "GuideBotWebhook/1.0"

    def log_message(self, format, *args):
        # Avoid writing user messages or request URLs to Railway logs.
        print("Meta webhook:", format % args)

    def send_plain(self, status: int, body: str):
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/instagram/webhook":
            self.send_plain(404, "Not found")
            return
        query = urllib.parse.parse_qs(parsed.query)
        mode = query.get("hub.mode", [""])[0]
        verify_token = query.get("hub.verify_token", [""])[0]
        challenge = query.get("hub.challenge", [""])[0]
        if mode == "subscribe" and META_VERIFY_TOKEN and hmac.compare_digest(
            verify_token, META_VERIFY_TOKEN
        ):
            self.send_plain(200, challenge)
        else:
            self.send_plain(403, "Verification failed")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/instagram/webhook":
            self.send_plain(404, "Not found")
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        signature = self.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            INSTAGRAM_APP_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        if not INSTAGRAM_APP_SECRET or not hmac.compare_digest(signature, expected):
            self.send_plain(403, "Invalid signature")
            return
        try:
            payload = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_plain(400, "Invalid JSON")
            return
        self.send_plain(200, "EVENT_RECEIVED")
        threading.Thread(target=process_instagram_webhook, args=(payload,), daemon=True).start()


def start_webhook_server():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MetaWebhookHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Instagram webhook server is listening on port {port}.")


def init_db():
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA_PATH)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            source TEXT,
            first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            guide_sent_at TEXT
        )"""
    )
    connection.commit()
    return connection


def save_user(connection, chat_id, source):
    connection.execute(
        "INSERT OR IGNORE INTO users (chat_id, source) VALUES (?, ?)", (chat_id, source or "direct")
    )
    connection.commit()


def start_menu(chat_id):
    """Show a persistent, simple entry button in the Telegram chat."""
    api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "Нажмите «▶️ Запустить», чтобы получить гайд.",
            "reply_markup": {
                "keyboard": [[{"text": "▶️ Запустить"}]],
                "resize_keyboard": True,
                "is_persistent": True,
            },
        },
    )


def is_channel_member(chat_id):
    """Check subscription. The bot must be an admin of the required channel."""
    try:
        member = api(
            "getChatMember", {"chat_id": REQUIRED_TELEGRAM_CHANNEL, "user_id": chat_id}
        )
    except RuntimeError as error:
        print(f"Subscription check failed: {error}", file=sys.stderr)
        return False
    status = member.get("status")
    return status in {"creator", "administrator", "member"} or (
        status == "restricted" and member.get("is_member", False)
    )


def subscription_prompt(chat_id):
    keyboard = {
        "inline_keyboard": [
            [{"text": "Подписаться на канал", "url": REQUIRED_TELEGRAM_CHANNEL_LINK}],
            [{"text": "Проверить подписку", "callback_data": "check_subscription"}],
        ]
    }
    api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": f"Чтобы получить гайд, подпишитесь на канал {REQUIRED_TELEGRAM_CHANNEL}.",
            "reply_markup": keyboard,
        },
    )


def send_welcome(chat_id):
    keyboard = {"inline_keyboard": [[{"text": "Получить гайд", "callback_data": "get_guide"}]]}
    api("sendMessage", {"chat_id": chat_id, "text": WELCOME, "reply_markup": keyboard})


def send_guide(connection, chat_id):
    missing = [str(path) for path in (VIDEO_PATH, PDF_PATH) if not path.is_file()]
    if missing:
        api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Материалы ещё загружаются. Пожалуйста, попробуйте немного позже.",
            },
        )
        print("Missing guide files:", ", ".join(missing), file=sys.stderr)
        return
    api("sendMessage", {"chat_id": chat_id, "text": "Готово — отправляю материалы 👇"})
    api("sendVideo", {"chat_id": chat_id, "caption": "Видео-инструкция"}, {"video": VIDEO_PATH})
    api("sendDocument", {"chat_id": chat_id, "caption": "PDF-инструкция"}, {"document": PDF_PATH})
    connection.execute(
        "UPDATE users SET guide_sent_at = CURRENT_TIMESTAMP WHERE chat_id = ?", (chat_id,)
    )
    connection.commit()


def handle_update(connection, update):
    message = update.get("message")
    callback = update.get("callback_query")

    if message:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            source = parts[1] if len(parts) == 2 else "direct"
            save_user(connection, chat_id, source)
            start_menu(chat_id)
        elif text.lower() in {"▶️ запустить", "запустить", "гайд", "получить гайд", "старт"}:
            save_user(connection, chat_id, "direct")
            if is_channel_member(chat_id):
                send_welcome(chat_id)
            else:
                subscription_prompt(chat_id)
        else:
            start_menu(chat_id)

    if callback and callback.get("data") in {"check_subscription", "get_guide"}:
        chat_id = callback["message"]["chat"]["id"]
        save_user(connection, chat_id, "direct")
        api("answerCallbackQuery", {"callback_query_id": callback["id"]})
        if not is_channel_member(chat_id):
            subscription_prompt(chat_id)
        elif callback["data"] == "check_subscription":
            send_welcome(chat_id)
        else:
            send_guide(connection, chat_id)


def main():
    if not TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting the bot.")
    start_webhook_server()
    if not (INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID):
        print("Instagram replies are disabled until Instagram variables are configured.")
    if not (INSTAGRAM_APP_SECRET and META_VERIFY_TOKEN):
        print("Meta webhook verification is disabled until its secrets are configured.")
    connection = init_db()
    offset = None
    print("Bot is running. Press Ctrl+C to stop.")
    while True:
        try:
            payload = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                payload["offset"] = offset
            for update in api("getUpdates", payload):
                offset = update["update_id"] + 1
                handle_update(connection, update)
        except KeyboardInterrupt:
            break
        except Exception as error:  # Keep the bot alive during temporary API errors.
            print(f"Error: {error}", file=sys.stderr)
            time.sleep(5)
    connection.close()


if __name__ == "__main__":
    main()
