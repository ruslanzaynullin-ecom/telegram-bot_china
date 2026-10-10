# Canton Fair guide bot

Free delivery bot for `@cantonfair_china_bot`.

## What it does

- accepts the Instagram tracking link `https://t.me/cantonfair_china_bot?start=instagram`;
- shows a **Получить гайд** button;
- sends the video and PDF after the button is pressed;
- stores only the chat ID, traffic source, first start, and guide-delivery timestamp in a local SQLite file.

## Local launch

1. Create `assets/` and put the materials there as `guide-video.mov` and `guide.pdf`.
2. Copy `.env.example` to `.env` and put the BotFather token in `TELEGRAM_BOT_TOKEN`.
3. Load environment variables and run the bot:

```bash
set -a; source .env; set +a
python3 bot.py
```

The token must stay only in `.env`; it is excluded from version control.

## Instagram message

> Привет! Получите бесплатный пошаговый гайд, как найти фабрики и их продукцию с Кантонской выставки в Китае 2026 года. Нажмите на ссылку, затем кнопку «Получить гайд» в Telegram: https://t.me/cantonfair_china_bot?start=instagram

## Replacing materials

Stop the bot, replace either file in `assets/` with the same filename, then start the bot again. The Telegram username and Instagram link stay unchanged.
