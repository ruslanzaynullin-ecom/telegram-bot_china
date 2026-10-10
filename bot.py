#!/usr/bin/env python3
"""Free guide bot for @cantonfair_china_bot.

Uses only the Python standard library. Keep the Telegram token in environment
variables; never paste it into this file.
"""

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
VIDEO_PATH = Path(os.environ.get("GUIDE_VIDEO_PATH", "assets/guide-video.mov"))
PDF_PATH = Path(os.environ.get("GUIDE_PDF_PATH", "assets/guide.pdf"))
DATA_PATH = Path(os.environ.get("DATA_PATH", "data/bot.sqlite3"))
API_ROOT = f"https://api.telegram.org/bot{TOKEN}"

WELCOME = (
    "Привет! 👋\n\n"
    "Здесь вы получите бесплатный пошаговый гайд: «Как найти фабрики и их "
    "продукцию с Кантонской выставки в Китае 2026 года».\n\n"
    "Нажмите кнопку — и я пришлю видео и PDF-инструкцию."
)


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
            send_welcome(chat_id)
        elif text.lower() in {"гайд", "получить гайд", "старт"}:
            save_user(connection, chat_id, "direct")
            send_welcome(chat_id)
        else:
            api("sendMessage", {"chat_id": chat_id, "text": "Нажмите /start, чтобы получить гайд."})

    if callback and callback.get("data") == "get_guide":
        chat_id = callback["message"]["chat"]["id"]
        save_user(connection, chat_id, "direct")
        api("answerCallbackQuery", {"callback_query_id": callback["id"]})
        send_guide(connection, chat_id)


def main():
    if not TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting the bot.")
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
