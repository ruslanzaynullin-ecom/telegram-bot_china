# Canton Fair guide bot

Free delivery bot for `@cantonfair_china_bot` (display name: «Автобот_поддержка»).

## What it does

- accepts the Instagram tracking link `https://t.me/cantonfair_china_bot?start=instagram`;
- shows a **Получить гайд** button;
- sends the video and PDF after the button is pressed;
- requires subscription to `@r_zaynullin` before the guide is available;
- shows a persistent **▶️ Запустить** button in the chat;
- stores only the chat ID, traffic source, first start, and guide-delivery timestamp in a local SQLite file.

## Local launch

1. Create `assets/` and put the materials there as `guide-video.mp4` and `guide.pdf`.
2. Copy `.env.example` to `.env` and put the BotFather token in `TELEGRAM_BOT_TOKEN`.
3. Load environment variables and run the bot:

```bash
set -a; source .env; set +a
python3 bot.py
```

The token must stay only in `.env`; it is excluded from version control.

## Instagram message

> Привет! Получите бесплатный пошаговый гайд, как найти фабрики и их продукцию с Кантонской выставки в Китае 2026 года. Нажмите на ссылку, затем кнопку «Получить гайд» в Telegram: https://t.me/cantonfair_china_bot?start=instagram

## Instagram Direct and comments

The bot can also receive Instagram webhooks at `/instagram/webhook`.
Set these Railway service variables (do not commit their values):

- `INSTAGRAM_ACCESS_TOKEN` — token generated in Meta for the main Instagram account.
- `INSTAGRAM_ACCOUNT_ID` — main connected Instagram account ID.
- `INSTAGRAM_PROFILE_URL` — main profile link used in the subscription prompt.
- `INSTAGRAM_2_ACCESS_TOKEN` — token for an optional second (child) Instagram account.
- `INSTAGRAM_2_ACCOUNT_ID` — second connected Instagram account ID.
- `INSTAGRAM_2_PROFILE_URL` — second profile link. The subscription button from this account leads here.
- `INSTAGRAM_APP_SECRET` — Instagram app secret from Meta.
- `META_VERIFY_TOKEN` — a new random secret that you create; it must exactly match the token entered in Meta Webhooks.
- `TELEGRAM_BOT_LINK` — optional; defaults to the current guide bot link.
- `MARKETCAR_INSTAGRAM_USERNAME` — defaults to `marketcar.pro`; identifies the child Instagram account.
- `MARKETCAR_TELEGRAM_BOT_LINK` — defaults to `https://t.me/cantonfair_china_bot?start=marketcar`.
- `MARKETCAR_TELEGRAM_CHANNEL` — defaults to `@marketcar_pro` and is required only for people arriving from `marketcar.pro`.
- `MARKETCAR_TELEGRAM_CHANNEL_LINK` — defaults to `https://t.me/marketcar_pro`.

### Connecting a second account through Instagram Business Login

This is the simplest and safest way to connect a child professional account:

1. In Railway, add `INSTAGRAM_OAUTH_CLIENT_ID` — the **Instagram App ID** from the Meta Instagram API setup page. `INSTAGRAM_APP_SECRET` is reused as the secret, so it is not duplicated.
2. In Meta, add this exact redirect URL in **Instagram Business Login**:
   `https://zippy-flow-production.up.railway.app/instagram/oauth/callback`
3. Open `https://zippy-flow-production.up.railway.app/instagram/connect` while signed into the child Instagram account and approve the requested access.

The bot stores the resulting account ID and token in its Railway database. No token is put in GitHub or copied into a browser field. The traditional `INSTAGRAM_2_*` variables remain supported if you prefer connecting it manually.

In Meta, use the public Railway address plus `/instagram/webhook` as the callback URL. Add both professional Instagram accounts to the same Meta app, generate a token for each one, and enable `messages` and `comments` webhook subscriptions for both accounts.

Flow:

1. A Direct message `гайд` receives the Instagram-profile subscription prompt.
2. After the user writes `готово`, the bot presents a confirmation action.
3. The confirmation sends the Telegram guide-bot link.
4. A comment `гайд` receives a public reply and a private Direct prompt where Meta permits it.

## Replacing materials

Replace either file in `assets/` with the same filename and commit it to GitHub. Railway redeploys automatically. The Telegram username and Instagram link stay unchanged.

## Required Telegram channel

The bot checks membership in `@r_zaynullin` before it sends the guide from the main Instagram account. Add the bot to that channel as an administrator; otherwise Telegram does not reliably allow it to check other users' membership. You can override the channel with the `REQUIRED_TELEGRAM_CHANNEL` and `REQUIRED_TELEGRAM_CHANNEL_LINK` Railway variables.

For requests from `marketcar.pro`, the Instagram bot first checks the person follows `marketcar.pro`, then sends a Telegram link with the `marketcar` source. The Telegram bot requires subscription to `@marketcar_pro` before sending the guide. Add the same Telegram bot as an administrator of `@marketcar_pro` as well.
