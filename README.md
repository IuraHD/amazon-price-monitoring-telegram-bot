# Amazon Price Monitor

A Telegram bot that checks Amazon UK product pages and alerts you when a tracked price changes. Each chat has its own subscriptions, folders, and alert rules. The bot shares product checks across chats so it does not fetch the same page separately for every subscriber.

It uses aiogram, aiohttp, Beautiful Soup, SQLite, and matplotlib. Prices come from Amazon's public product-page HTML, not an Amazon product API.

## What it does

- Add an Amazon product URL or supported short link, then place it in a folder. Rename folders, change their emoji, or move products without resetting alerts.
- Browse paginated product lists, search and sort them, and see recorded-price totals.
- Check prices on demand or on a schedule. Manual and scheduled checks share a cooldown and a database lease, so they do not fetch the same product at the same time.
- Alert on any price change, a drop, a target-price crossing, or a minimum percentage change. Stock alerts require a change from unavailable to available.
- Set quiet hours in your own timezone. Deferred alerts and delivery retries survive bot restarts.
- View 7-, 30-, or 90-day price charts and export 90 days of observations as CSV.

## Install and run

Use Python 3.11–3.13 and a token from [BotFather](https://t.me/BotFather). With [uv](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/IuraHD/amazon-price-monitoring-telegram-bot.git
cd amazon-price-monitoring-telegram-bot
uv sync --locked --no-dev --python 3.12
```

Copy `.env.example` to `.env`, set `TELEGRAM_BOT_TOKEN`, then start the bot:

```sh
uv run --locked --no-dev python -m app.bot.bot
```

If you prefer pip, create a virtual environment and install the hash-locked `requirements.txt`:

```sh
python -m venv .venv
# PowerShell: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
python -m app.bot.bot
```

Run **one polling process per token**. The bot stores its data in SQLite; for a release-based deployment, put the database outside the checkout. See [deployment and rollback](docs/deployment.md) before replacing a running service.

## Use the bot

Send `/start`, choose **Add product**, paste a URL, and select or create a folder. Adding the same product again opens its existing subscription without resetting its alerts. From a product card you can refresh the price, move it, set a target, mute alerts, change alert rules, view history, or export CSV. Deleting a product or folder requires confirmation.

| Command | Action |
| --- | --- |
| `/start` | Open the menu. |
| `/cancel` | Leave the current input step. |
| `/quiet 22 8 Europe/London` | Defer alerts from 22:00 until 08:00 in the named timezone. |
| `/quiet off` | Disable quiet hours. |

Quiet hours follow daylight-saving changes. Deferred messages resume afterward; they are not combined into a daily digest. A target alert fires only when an observed price crosses from above the target to at or below it. Muting cancels queued alerts. Blocked chats are paused until `/start` is received again. Telegram timeouts can leave message acceptance uncertain, so retries cannot guarantee exactly-once delivery.

**Refresh my products** checks only the current chat's subscriptions. A new observation for a shared product can still alert its other subscribers according to their own rules. Manual and scheduled checks share a 60-second cooldown.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Required | BotFather token; keep it out of Git. |
| `TELEGRAM_PRIMARY_CHAT_ID` | Unset | Restrict access and scheduled delivery to one chat. Group IDs can be negative. |
| `PRICE_REFRESH_MINUTES` | `10` | Interval after a successful check, from 5 to 1440 minutes. Failed checks wait at least 15 minutes before a scheduled retry. |
| `DATABASE_PATH` | `app/data/tracker.db` | SQLite file. Use an absolute shared path when deploying releases. |

Private chats work by default. To use a group, set its ID in `TELEGRAM_PRIMARY_CHAT_ID`; only group admins can manage tracking. Invalid configuration stops startup rather than silently disabling restrictions.

## Price data and limitations

The parser verifies the product ASIN and reads recognized primary price elements, including the selected one-time-purchase offer. Blocked, ambiguous, or unfamiliar pages are recorded as failed checks, not guessed prices. Prices are stored as integer pence in GBP. Shipping, vouchers, Prime-only prices, and subscription discounts are not included. Seller information may be displayed, but the bot does not pin a seller across checks. Confirm the current offer on Amazon before buying.

Links from other Amazon marketplaces require confirmation before tracking the same ASIN on Amazon UK; no currency conversion takes place. Product cards show the last recorded price, the last successful check, the last attempt, and the current status. List totals use recorded prices, exclude unknown prices, and are not live basket quotes.

Limits are 200 tracked products and 100 folders per chat. Charts retain endpoints and price extremes within a 200-point display limit; CSV exports include every observation in the selected range. Failed checks do not create price observations. Amazon can change its markup or block automated requests; those cases appear as check failures and may require parser maintenance.

## Data and upgrades

The database separates products, chat subscriptions, observations, folders, preferences, and a durable notification outbox. Removing a subscription leaves other chats' data intact; product history is deleted when the last subscription is removed.

On the first start with the original database, the bot creates a timestamped `.bak` file before an atomic migration. It preserves valid subscriptions, folders, alert settings, and linked history. Broken folder assignments become Uncategorized; orphaned history stays in the backup. Legacy prices remain unverified until a new successful check, and that first new observation does not trigger a price-change alert. Legacy timestamps without a timezone are interpreted as UTC. Keep a separate backup before deploying.

## Development

```sh
uv sync --locked
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest -q
```

CI runs the tests on Python 3.11, 3.12, and 3.13 on Linux and Windows. When dependencies change, regenerate both lock outputs with `uv lock` and `uv export --no-dev --format requirements-txt --output-file requirements.txt`.

Licensed under [MIT](LICENSE).
