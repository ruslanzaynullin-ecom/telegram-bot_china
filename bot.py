#!/usr/bin/env python3
"""Free guide bot for @cantonfair_china_bot.

Uses only the Python standard library. Keep the Telegram token in environment
variables; never paste it into this file.
"""

import json
import html
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
VIDEO_PATH = Path(os.environ.get("GUIDE_VIDEO_PATH", "assets/guide-video.mp4"))
PDF_PATH = Path(os.environ.get("GUIDE_PDF_PATH", "assets/guide.pdf"))
DATA_PATH = Path(os.environ.get("DATA_PATH", "data/bot.sqlite3"))
API_ROOT = f"https://api.telegram.org/bot{TOKEN}"

# Instagram / Meta configuration. Keep all secrets in Railway variables.
INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
INSTAGRAM_PROFILE_URL = os.environ.get("INSTAGRAM_PROFILE_URL", "")
# Optional second professional account. It uses the same Meta app, webhook,
# Telegram bot and guide files as the primary account.
INSTAGRAM_2_ACCESS_TOKEN = os.environ.get("INSTAGRAM_2_ACCESS_TOKEN", "")
INSTAGRAM_2_ACCOUNT_ID = os.environ.get("INSTAGRAM_2_ACCOUNT_ID", "")
INSTAGRAM_2_PROFILE_URL = os.environ.get("INSTAGRAM_2_PROFILE_URL", "")
INSTAGRAM_APP_SECRET = os.environ.get("INSTAGRAM_APP_SECRET", "")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "")
# Instagram Business Login is used to connect an additional professional
# account without pasting its token into Railway by hand. The client secret
# intentionally falls back to the existing Instagram app secret.
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://zippy-flow-production.up.railway.app"
).rstrip("/")
INSTAGRAM_OAUTH_CLIENT_ID = os.environ.get("INSTAGRAM_OAUTH_CLIENT_ID", "")
INSTAGRAM_OAUTH_CLIENT_SECRET = os.environ.get(
    "INSTAGRAM_OAUTH_CLIENT_SECRET", INSTAGRAM_APP_SECRET
)
INSTAGRAM_OAUTH_REDIRECT_URI = os.environ.get(
    "INSTAGRAM_OAUTH_REDIRECT_URI",
    f"{PUBLIC_BASE_URL}/instagram/oauth/callback",
)
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

# Markers are deliberately broad: people often write a full request rather
# than a single trigger word below a post or in Direct.
INSTAGRAM_GUIDE_MARKERS = ("гайд", "гаид", "guide", "инструкц", "как получить")
INSTAGRAM_START_WORDS = {"start", "старт"}
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


def configured_instagram_accounts():
    """Return every fully configured Instagram account.

    The original variables continue to represent the main account. The optional
    ``INSTAGRAM_2_*`` variables add the child account without changing the
    already working setup.
    """
    accounts = []
    for label, account_id, token, profile_url in (
        ("main", INSTAGRAM_ACCOUNT_ID, INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_PROFILE_URL),
        ("second", INSTAGRAM_2_ACCOUNT_ID, INSTAGRAM_2_ACCESS_TOKEN, INSTAGRAM_2_PROFILE_URL),
    ):
        if account_id and token:
            accounts.append(
                {
                    "label": label,
                    "id": str(account_id),
                    "token": token,
                    "profile_url": profile_url,
                }
            )
    # Accounts connected through Instagram Business Login live in the local
    # database, so their access token is never exposed in a public URL or
    # committed to GitHub.
    known_ids = {account["id"] for account in accounts}
    for account in oauth_instagram_accounts():
        if account["id"] not in known_ids:
            accounts.append(account)
    return accounts


def oauth_instagram_accounts():
    """Load professional accounts connected through Instagram Business Login."""
    if not DATA_PATH.is_file():
        return []
    connection = sqlite3.connect(DATA_PATH)
    try:
        rows = connection.execute(
            "SELECT account_id, access_token, profile_url FROM instagram_connected_accounts"
        ).fetchall()
        return [
            {
                "label": "oauth",
                "id": str(account_id),
                "token": token,
                "profile_url": profile_url or "",
            }
            for account_id, token, profile_url in rows
            if account_id and token
        ]
    except sqlite3.OperationalError:
        # The server can receive an early Meta health check during its first
        # startup, before the database schema has been created.
        return []
    finally:
        connection.close()


def account_for_webhook_entry(entry: dict):
    """Find the professional account that received a webhook event."""
    entry_id = str(entry.get("id") or "")
    for account in configured_instagram_accounts():
        if account["id"] == entry_id:
            return account
    # Some Meta test payloads omit entry.id. Preserve the existing single
    # account behavior, but never guess when two accounts are configured.
    accounts = configured_instagram_accounts()
    return accounts[0] if len(accounts) == 1 else None


def instagram_api(account: dict, path: str, payload: dict):
    """Call the Instagram API without ever logging its access token."""
    if not account:
        raise RuntimeError("Instagram API is not configured.")
    request = urllib.request.Request(
        f"{INSTAGRAM_API_ROOT}/{path.lstrip('/')}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {account['token']}",
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


def instagram_follows_business(account: dict, user_id: str):
    """Return whether this Direct participant follows our professional account.

    The Instagram User Profile API only accepts the Instagram-scoped ID supplied
    by a Direct webhook. It cannot be used to look up arbitrary usernames.
    """
    if not account:
        raise RuntimeError("Instagram API is not configured.")
    query = urllib.parse.urlencode({"fields": "is_user_follow_business"})
    request = urllib.request.Request(
        f"{INSTAGRAM_API_ROOT}/{user_id}?{query}",
        headers={"Authorization": f"Bearer {account['token']}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            profile = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"Instagram profile lookup error {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Instagram profile lookup unavailable: {error}") from error
    result = profile.get("is_user_follow_business")
    return result if isinstance(result, bool) else None


def send_instagram_text(account: dict, recipient_id: str, text: str, quick_replies=None):
    message = {"text": text}
    if quick_replies:
        message["quick_replies"] = quick_replies
    return instagram_api(
        account,
        # The token is issued to the Instagram professional account. Using
        # `me` avoids an object-access error when Meta maps that account ID
        # differently inside the Instagram Login flow.
        "me/messages",
        {"recipient": {"id": str(recipient_id)}, "message": message},
    )


def send_instagram_buttons(account: dict, recipient: dict, text: str, buttons: list[dict]):
    """Send a Meta button template to a Direct user or comment author."""
    return instagram_api(
        account,
        "me/messages",
        {
            "recipient": recipient,
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {"template_type": "button", "text": text, "buttons": buttons},
                }
            },
        },
    )


def subscription_buttons(account: dict):
    buttons = []
    if account["profile_url"]:
        buttons.append(
            {
                "type": "web_url",
                "url": account["profile_url"],
                "title": "Подписаться",
            }
        )
    buttons.append(
        {
            "type": "postback",
            "title": "Проверить подписку",
            "payload": "confirm_subscription",
        }
    )
    return buttons


def instagram_subscription_prompt(account: dict, recipient_id: str):
    text = (
        "Чтобы получить гайд, подпишитесь на наш Instagram. "
        "Затем вернитесь сюда и нажмите «Проверить подписку»."
    )
    try:
        return send_instagram_buttons(account, {"id": str(recipient_id)}, text, subscription_buttons(account))
    except RuntimeError as error:
        print(f"Instagram buttons failed, using a link: {error}", file=sys.stderr)
        if account["profile_url"]:
            text = f"{text}\n\n{account['profile_url']}"
        return send_instagram_text(account, recipient_id, text)


def instagram_confirmation_prompt(account: dict, recipient_id: str):
    text = "Если вы уже подписались, нажмите кнопку ниже для проверки."
    try:
        return send_instagram_buttons(
            account,
            {"id": str(recipient_id)},
            text,
            [{"type": "postback", "title": "Проверить подписку", "payload": "confirm_subscription"}],
        )
    except RuntimeError as error:
        print(f"Instagram confirmation button failed: {error}", file=sys.stderr)
        return send_instagram_text(account, recipient_id, "Напишите «подтверждаю», чтобы получить ссылку.")


def send_telegram_link(account: dict, recipient_id: str):
    result = send_instagram_text(
        account,
        recipient_id,
        f"Спасибо! Вот ссылка на Telegram-бота с гайдом:\n{TELEGRAM_BOT_LINK}",
    )
    mark_instagram_user_complete(account["id"], recipient_id)
    return result


def check_instagram_subscription(account: dict, recipient_id: str):
    """Release the Telegram link only after Meta confirms the follow status."""
    try:
        follows = instagram_follows_business(account, recipient_id)
    except RuntimeError as error:
        # A temporary API problem must never be presented as "you are not
        # subscribed". The person can simply retry the check shortly.
        print(f"Instagram follow check failed: {error}", file=sys.stderr)
        send_instagram_text(
            account,
            recipient_id,
            "Не удалось проверить подписку прямо сейчас. Подождите минуту и нажмите «Готово, подписался» ещё раз.",
        )
        return instagram_confirmation_prompt(account, recipient_id)

    if follows is True:
        return send_telegram_link(account, recipient_id)

    if follows is False:
        message = "Пока не вижу подписку. Подпишитесь на аккаунт и попробуйте ещё раз через минуту."
        if account["profile_url"]:
            message += f"\n\n{account['profile_url']}"
        send_instagram_text(account, recipient_id, message)
        return instagram_confirmation_prompt(account, recipient_id)

    send_instagram_text(
        account,
        recipient_id,
        "Статус подписки пока не обновился. Подождите минуту и нажмите «Готово, подписался» ещё раз.",
    )
    return instagram_confirmation_prompt(account, recipient_id)


def reply_to_instagram_comment(account: dict, comment_id: str, text: str):
    return instagram_api(account, f"{comment_id}/replies", {"message": text})


def private_reply_to_instagram_comment(account: dict, comment_id: str, text: str):
    """Start a permitted private reply from a public Instagram comment.

    Instagram Login uses the Send API for this: the comment ID belongs in the
    recipient object. The older ``/<comment-id>/private_replies`` edge is not
    available to this type of Instagram access token.
    """
    return send_instagram_buttons(
        account, {"comment_id": str(comment_id)}, text, subscription_buttons(account)
    )


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


def is_instagram_guide_request(text: str) -> bool:
    """Recognize common Russian and English requests for the guide."""
    normalized = " ".join(text.strip().lower().split())
    return normalized in INSTAGRAM_START_WORDS or any(
        marker in normalized for marker in INSTAGRAM_GUIDE_MARKERS
    )


def handle_instagram_message(account: dict, event: dict):
    sender = (event.get("sender") or {}).get("id")
    if not sender:
        return
    # Once the guide is delivered, this conversation belongs to the person
    # managing the account. The automation must stay silent.
    if instagram_user_is_complete(account["id"], sender):
        return
    message = event.get("message") or {}
    postback = event.get("postback") or {}
    text = (message.get("text") or "").strip().lower()
    payload = (postback.get("payload") or "").strip()
    event_id = message.get("mid") or postback.get("mid") or f"{sender}:{text}:{payload}"
    if not remember_event(event_id):
        return

    if payload == "confirm_subscription":
        check_instagram_subscription(account, sender)
    elif is_instagram_guide_request(text):
        instagram_subscription_prompt(account, sender)
    # Any other Direct message belongs to the account owner. Stay quiet so a
    # normal manual conversation is never interrupted by the automation.


def handle_instagram_comment(account: dict, change: dict):
    value = change.get("value") or {}
    comment_id = str(value.get("id") or "")
    commenter_id = str((value.get("from") or {}).get("id") or "")
    text = (value.get("text") or "").strip().lower()
    if not comment_id or not remember_event(comment_id):
        return
    if commenter_id and instagram_user_is_complete(account["id"], commenter_id):
        # Keep a polite public response on later comments, but do not restart
        # the Direct automation or send the guide again. The account owner can
        # continue the resulting Direct conversation manually.
        reply_to_instagram_comment(account, comment_id, "Проверьте Direct ✉️")
        return
    if not is_instagram_guide_request(text):
        return

    # A public reply makes the next action clear. Meta permits a single private
    # reply to an eligible comment, which starts the Direct conversation.
    reply_to_instagram_comment(account, comment_id, "Проверьте Direct ✉️")
    try:
        private_reply_to_instagram_comment(
            account,
            comment_id,
            "Чтобы получить гайд, подпишитесь на наш Instagram и затем нажмите «Проверить подписку».",
        )
    except RuntimeError as error:
        # Keep the public comment reply even if the private reply cannot be
        # delivered (for example, when Meta's time window does not allow it).
        print(f"Instagram private comment reply failed: {error}", file=sys.stderr)


def process_instagram_webhook(payload: dict):
    for entry in payload.get("entry", []):
        account = account_for_webhook_entry(entry)
        if not account:
            print("Instagram webhook ignored: account is not configured.", file=sys.stderr)
            continue
        for message in entry.get("messaging", []):
            try:
                handle_instagram_message(account, message)
            except Exception as error:
                print(f"Instagram message handling error: {error}", file=sys.stderr)
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue
            try:
                handle_instagram_comment(account, change)
            except Exception as error:
                print(f"Instagram comment handling error: {error}", file=sys.stderr)


def oauth_connect_url(state: str):
    """Build the Instagram Business Login URL for an account owner."""
    query = urllib.parse.urlencode(
        {
            "client_id": INSTAGRAM_OAUTH_CLIENT_ID,
            "redirect_uri": INSTAGRAM_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": (
                "instagram_business_basic,instagram_business_manage_messages,"
                "instagram_business_manage_comments"
            ),
            "state": state,
        }
    )
    return f"https://www.instagram.com/oauth/authorize?{query}"


def create_oauth_state():
    """Create a short-lived one-use value protecting the OAuth callback."""
    state = uuid.uuid4().hex
    connection = sqlite3.connect(DATA_PATH)
    try:
        cutoff = time.time() - 15 * 60
        connection.execute("DELETE FROM instagram_oauth_states WHERE created_at < ?", (cutoff,))
        connection.execute(
            "INSERT INTO instagram_oauth_states (state, created_at) VALUES (?, ?)",
            (state, time.time()),
        )
        connection.commit()
    finally:
        connection.close()
    return state


def consume_oauth_state(state: str) -> bool:
    """Validate and delete a one-use OAuth state value."""
    connection = sqlite3.connect(DATA_PATH)
    try:
        row = connection.execute(
            "SELECT created_at FROM instagram_oauth_states WHERE state = ?", (state,)
        ).fetchone()
        connection.execute("DELETE FROM instagram_oauth_states WHERE state = ?", (state,))
        connection.commit()
        return bool(row and float(row[0]) >= time.time() - 15 * 60)
    finally:
        connection.close()


def oauth_request_json(url: str, form: dict | None = None):
    """Call the Instagram OAuth API without logging authorization codes or tokens."""
    if form is None:
        request = urllib.request.Request(url, method="GET")
    else:
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        # OAuth providers sometimes return descriptive payloads. Do not put
        # them in logs: an authorization code may be included in a URL.
        raise RuntimeError(f"Instagram authorization failed (HTTP {error.code}).") from error
    except urllib.error.URLError as error:
        raise RuntimeError("Instagram authorization service is unavailable.") from error


def connect_instagram_account(code: str):
    """Exchange an OAuth code and securely save the connected account."""
    if not (INSTAGRAM_OAUTH_CLIENT_ID and INSTAGRAM_OAUTH_CLIENT_SECRET):
        raise RuntimeError("Instagram Business Login is not configured on the server.")
    short_lived = oauth_request_json(
        "https://api.instagram.com/oauth/access_token",
        {
            "client_id": INSTAGRAM_OAUTH_CLIENT_ID,
            "client_secret": INSTAGRAM_OAUTH_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": INSTAGRAM_OAUTH_REDIRECT_URI,
            "code": code,
        },
    )
    short_token = short_lived.get("access_token")
    if not short_token:
        raise RuntimeError("Instagram did not return an access token.")
    query = urllib.parse.urlencode(
        {
            "grant_type": "ig_exchange_token",
            "client_secret": INSTAGRAM_OAUTH_CLIENT_SECRET,
            "access_token": short_token,
        }
    )
    long_lived = oauth_request_json(f"https://graph.instagram.com/access_token?{query}")
    access_token = long_lived.get("access_token") or short_token
    profile_query = urllib.parse.urlencode({"fields": "user_id,username", "access_token": access_token})
    profile = oauth_request_json(f"{INSTAGRAM_API_ROOT}/me?{profile_query}")
    account_id = str(profile.get("user_id") or short_lived.get("user_id") or "")
    username = str(profile.get("username") or "")
    if not account_id:
        raise RuntimeError("Instagram did not return the professional account ID.")
    profile_url = f"https://www.instagram.com/{username}/" if username else ""
    connection = sqlite3.connect(DATA_PATH)
    try:
        connection.execute(
            """INSERT INTO instagram_connected_accounts
            (account_id, access_token, username, profile_url, connected_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id) DO UPDATE SET
                access_token = excluded.access_token,
                username = excluded.username,
                profile_url = excluded.profile_url,
                connected_at = CURRENT_TIMESTAMP""",
            (account_id, access_token, username, profile_url),
        )
        connection.commit()
    finally:
        connection.close()
    return username or account_id


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

    def send_html(self, status: int, body: str):
        """Serve the public policy pages required by Meta app settings."""
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/instagram/connect":
            if not (INSTAGRAM_OAUTH_CLIENT_ID and INSTAGRAM_OAUTH_CLIENT_SECRET):
                self.send_plain(503, "Instagram Business Login is not configured yet.")
                return
            state = create_oauth_state()
            self.send_response(302)
            self.send_header("Location", oauth_connect_url(state))
            self.end_headers()
            return
        if parsed.path == "/instagram/oauth/callback":
            query = urllib.parse.parse_qs(parsed.query)
            state = query.get("state", [""])[0]
            error = query.get("error", [""])[0]
            code = query.get("code", [""])[0]
            if error:
                self.send_html(
                    400,
                    "<!doctype html><meta charset=\"utf-8\"><title>Подключение не завершено</title>"
                    "<p>Подключение Instagram отменено или не было разрешено. Закройте эту страницу и попробуйте ещё раз.</p>",
                )
                return
            if not state or not code or not consume_oauth_state(state):
                self.send_html(
                    400,
                    "<!doctype html><meta charset=\"utf-8\"><title>Ссылка устарела</title>"
                    "<p>Ссылка для подключения устарела. Закройте эту страницу и начните подключение заново.</p>",
                )
                return
            try:
                account_name = connect_instagram_account(code)
            except RuntimeError as error:
                print(f"Instagram Business Login failed: {error}", file=sys.stderr)
                self.send_html(
                    502,
                    "<!doctype html><meta charset=\"utf-8\"><title>Не удалось подключить</title>"
                    "<p>Не удалось завершить подключение Instagram. Вернитесь в Meta и попробуйте ещё раз.</p>",
                )
                return
            self.send_html(
                200,
                "<!doctype html><meta charset=\"utf-8\"><title>Instagram подключён</title>"
                f"<p>Аккаунт <strong>{html.escape(account_name)}</strong> подключён. Эту страницу можно закрыть.</p>",
            )
            return
        if parsed.path == "/privacy":
            self.send_html(
                200,
                """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Политика конфиденциальности — Guide Bot</title>
<style>body{font:17px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:760px;margin:48px auto;padding:0 24px;color:#1f2937}h1{line-height:1.2}a{color:#2563eb}</style>
</head><body><h1>Политика конфиденциальности Guide Bot</h1>
<p>Дата вступления в силу: 10 октября 2026 года.</p>
<p>Guide Bot помогает пользователям получить материалы о Кантонской выставке через Instagram и Telegram.</p>
<h2>Какие данные обрабатываются</h2>
<p>Для ответа на сообщения и комментарии мы можем обрабатывать идентификатор пользователя Instagram, текст сообщения или комментария и технические данные, которые передаёт Meta. Для доставки материалов в Telegram бот сохраняет идентификатор Telegram-чата, источник перехода и время отправки гайда.</p>
<h2>Зачем это нужно</h2>
<p>Данные используются только для работы автоответчика, отправки запрошенной ссылки на Telegram-бота, защиты от повторной отправки и технической поддержки. Мы не продаём персональные данные и не используем их для рекламы третьих лиц.</p>
<h2>Хранение и защита</h2>
<p>Секреты доступа хранятся в защищённых переменных окружения. Доступ к данным ограничен владельцем сервиса и используется только для его работы.</p>
<h2>Ваши права и удаление данных</h2>
<p>Чтобы запросить сведения об обработке или удаление данных, напишите на <a href="mailto:turkey_original_brand@bk.ru?subject=Guide%20Bot%20%E2%80%94%20%D1%83%D0%B4%D0%B0%D0%BB%D0%B5%D0%BD%D0%B8%D0%B5%20%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D1%85">turkey_original_brand@bk.ru</a>. Укажите свой Instagram username или Telegram username, чтобы мы смогли найти запрос.</p>
<p>Подробная инструкция: <a href="/data-deletion">удаление данных пользователя</a>.</p>
</body></html>""",
            )
            return
        if parsed.path == "/data-deletion":
            self.send_html(
                200,
                """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Удаление данных — Guide Bot</title>
<style>body{font:17px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:760px;margin:48px auto;padding:0 24px;color:#1f2937}h1{line-height:1.2}a{color:#2563eb}</style>
</head><body><h1>Удаление данных пользователя</h1>
<p>Чтобы удалить данные, связанные с Guide Bot, отправьте письмо на <a href="mailto:turkey_original_brand@bk.ru?subject=Guide%20Bot%20%E2%80%94%20%D1%83%D0%B4%D0%B0%D0%BB%D0%B5%D0%BD%D0%B8%D0%B5%20%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D1%85">turkey_original_brand@bk.ru</a> с темой «Удаление данных Guide Bot».</p>
<p>В письме укажите ваш Instagram username или Telegram username. Мы подтвердим получение запроса и удалим доступные данные, связанные с ним, в течение 30 дней, если их дальнейшее хранение не требуется по закону.</p>
<p><a href="/privacy">Вернуться к политике конфиденциальности</a>.</p>
</body></html>""",
            )
            return
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
    connection.execute(
        """CREATE TABLE IF NOT EXISTS completed_instagram_users (
            user_id TEXT PRIMARY KEY,
            completed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS completed_instagram_users_by_account (
            account_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            completed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (account_id, user_id)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS instagram_oauth_states (
            state TEXT PRIMARY KEY,
            created_at REAL NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS instagram_connected_accounts (
            account_id TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            username TEXT,
            profile_url TEXT,
            connected_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.commit()
    return connection


def instagram_user_is_complete(account_id, user_id):
    """Check completion in a short-lived connection safe for webhook threads."""
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA_PATH)
    try:
        row = connection.execute(
            "SELECT 1 FROM completed_instagram_users_by_account "
            "WHERE account_id = ? AND user_id = ?",
            (str(account_id), str(user_id)),
        ).fetchone()
        # Preserve the completed state created by the first version of the
        # bot for the main account.
        if row is None and str(account_id) == str(INSTAGRAM_ACCOUNT_ID):
            row = connection.execute(
                "SELECT 1 FROM completed_instagram_users WHERE user_id = ?", (str(user_id),)
            ).fetchone()
        return row is not None
    finally:
        connection.close()


def mark_instagram_user_complete(account_id, user_id):
    """Persist the handoff from automation to manual conversation."""
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA_PATH)
    try:
        connection.execute(
            "INSERT OR IGNORE INTO completed_instagram_users_by_account "
            "(account_id, user_id) VALUES (?, ?)",
            (str(account_id), str(user_id)),
        )
        connection.commit()
    finally:
        connection.close()


def save_user(connection, chat_id, source):
    connection.execute(
        "INSERT OR IGNORE INTO users (chat_id, source) VALUES (?, ?)", (chat_id, source or "direct")
    )
    connection.commit()


def telegram_user_is_complete(connection, chat_id):
    row = connection.execute(
        "SELECT guide_sent_at FROM users WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    return bool(row and row[0])


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
        if telegram_user_is_complete(connection, chat_id):
            return
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
        if telegram_user_is_complete(connection, chat_id):
            api("answerCallbackQuery", {"callback_query_id": callback["id"]})
            return
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
    connection = init_db()
    start_webhook_server()
    if not configured_instagram_accounts():
        print("Instagram replies are disabled until Instagram variables are configured.")
    if not (INSTAGRAM_APP_SECRET and META_VERIFY_TOKEN):
        print("Meta webhook verification is disabled until its secrets are configured.")
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
