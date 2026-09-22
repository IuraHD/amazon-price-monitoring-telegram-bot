# Amazon Price Monitor

A Telegram bot for tracking Amazon UK prices. I use Python, aiogram, aiohttp,
Beautiful Soup, SQLite, and matplotlib. Product prices are shared between checks;
each chat has its own subscriptions, folders, and alert preferences.

## Features

- Track a product by URL, including supported Amazon short links.
- Organize subscriptions into folders; rename folders, change their emoji, or
  move products without losing alert settings.
- Browse product names with pagination, search, sorting, and recorded-price totals.
- Check prices manually or on a schedule, with a shared per-product cooldown.
- Receive alerts for any change, drops only, a target-price crossing, or a minimum
  percentage change. Optional stock alerts require an explicit unavailable-to-
  available transition.
- Defer notifications during quiet hours in a chosen timezone.
- View 7-, 30-, or 90-day charts and export 90 days of observations as CSV.

## What a price means

The bot reads Amazon product-page HTML; it does not use an Amazon product API.
It verifies the ASIN and reads recognized primary price elements, including the
selected one-time-purchase accordion. Ambiguous, blocked, and unrecognized pages
are recorded as failures rather than guessed prices.

Prices are GBP amounts stored as integer pence. Shipping, vouchers, Prime-only
prices, and subscription discounts are not calculated. Seller information is
shown when available, but a seller is not pinned across checks. Check the offer
on Amazon before buying.

Links from other marketplaces require confirmation before tracking the same ASIN
on Amazon UK. This is not currency conversion. A product card shows the last
recorded price, last successful check, last attempt, and current check status.
List totals sum recorded prices, exclude unknown prices, and identify items that
need another check; they are not live basket quotes.

## Setup

Use Python 3.11–3.13 and a Telegram token from [@BotFather](https://t.me/BotFather).
The tested dependency versions are recorded in `uv.lock`.

```sh
git clone https://github.com/IuraHD/amazon-price-monitoring-telegram-bot.git
cd amazon-price-monitoring-telegram-bot
uv sync --locked --no-dev --python 3.12
```

Copy `.env.example` to `.env` and set the token. Then run:

```sh
uv run --locked --no-dev python -m app.bot.bot
```

Alternatively, create and activate a virtual environment, then install the
hash-locked requirements:

```sh
python -m venv .venv
# PowerShell: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
python -m app.bot.bot
```

The bot uses long polling. Run one polling process per token.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Required | Telegram token; never commit it. |
| `TELEGRAM_PRIMARY_CHAT_ID` | Unset | Restrict every interaction and scheduled delivery to one chat. Signed group IDs are supported. |
| `PRICE_REFRESH_MINUTES` | `10` | Interval after each completed successful check; integer from 5 to 1440. Failed checks wait at least 15 minutes before scheduled retry. |
| `DATABASE_PATH` | `app/data/tracker.db` | SQLite file; use an absolute shared path for release-based deployments. |

Invalid configuration stops startup rather than silently disabling restrictions.
Private chats are supported by default. Group use requires that group's ID in
`TELEGRAM_PRIMARY_CHAT_ID`; only group administrators can manage tracking.

## Commands and navigation

Send `/start`, then **Add product**, paste a URL, and select or create a folder.
Adding a duplicate opens the existing subscription without changing its settings.
Use **Move** to change its folder. Product and folder deletion require confirmation.
Use `/cancel` to abandon an input step.

Open a product to refresh it, set its target, mute alerts, change alert rules,
view history, or export CSV. A target alert fires when a previously observed price
crosses from above the target to at or below it. Muting cancels queued alerts.
A percentage filter compares consecutive successful observations.

To defer notifications overnight:

```text
/quiet 22 8 Europe/London
/quiet off
```

The timezone follows daylight-saving changes. Deferred alerts resume after quiet
hours; this is not a daily digest. Delivery retries survive restarts. Blocked
chats are paused until `/start` is received again. Telegram timeouts can leave
acceptance uncertain, so retry delivery cannot guarantee exactly-once messages.

Manual and scheduled checks share a 60-second cooldown and a database lease to
avoid overlapping requests. Manual **Refresh my products** is scoped to the
current chat. A shared product's new observation can notify its other subscribers
according to their own rules. Results report changed, unchanged, failed, and
skipped counts.

## Storage and upgrades

The schema separates `catalog`, `subscriptions`, `observations`, `folders`,
`preferences`, and a durable notification `outbox`. Foreign keys are enabled on
every connection. Removing a subscription does not remove another chat's data;
product history is deleted when the last subscription is removed.

The first start against the original database creates a timestamped `.bak` file
before an atomic migration. It preserves subscription IDs, folders, alert flags,
and valid linked history. Broken folder assignments become Uncategorized;
orphaned history remains in the backup. Legacy prices are marked unverified until
a successful new check, and the first new observation does not trigger a price
change alert. Legacy timestamps without a timezone are interpreted as UTC.

Keep an additional backup before deployment. See [deployment and rollback](docs/deployment.md).

## Development

```sh
uv sync --locked
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest -q
```

Tests cover migrations, ownership, parser failures, request validation, quiet
hours, delivery retries, concurrency, and Telegram interaction flows with mocked
network calls. CI checks Python 3.11, 3.12, and 3.13 on Linux and Windows.

After changing dependencies, regenerate both lock outputs:

```sh
uv lock
uv export --no-dev --format requirements-txt --output-file requirements.txt
```

## Layout

```text
app/bot/           Telegram handlers, keyboards, and input states
app/core/db.py     Schema, connections, and legacy migration
app/core/products.py  Chat-scoped subscriptions, observations, and alert rules
app/core/folders.py    Chat-scoped folder operations
app/core/service.py   Scheduled checks and durable notification delivery
app/utils/fetch.py    Validated URLs, bounded HTTP requests, and offer parsing
app/utils/graph.py    Off-thread PNG chart rendering
app/health.py         Read-only database health report
```

Limits are 200 tracked products and 100 folders per chat. Charts preserve endpoints
and price extremes within a 200-point display limit; exports include all samples
in their date range. No observation is invented for a failed check. Amazon can
still change its markup or block automated requests; those cases require parser
maintenance and are visible as check failures.

## License

[MIT](LICENSE).
