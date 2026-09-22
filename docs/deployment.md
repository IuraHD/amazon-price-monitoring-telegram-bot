# Deployment and rollback

Run one polling process per bot token. Prepare dependencies and validate a
release before stopping the existing service. Deploy a specific pushed commit
from a clean detached worktree; keep the existing checkout intact.

## Before switching

1. Confirm the service working directory, running revision, Python version, and
   database path. Preserve its existing environment and secrets.
2. Fetch the pushed revision and create a release worktree outside the existing
   checkout. Build its own Python 3.12 environment and install with
   `pip install --require-hashes -r requirements.txt`.
3. Run the offline tests and rehearse migration on a copy of the live database.
   Check subscription and folder counts, history ownership, and SQLite integrity.
4. Save the current systemd unit/drop-ins and database. Stop the bot before the
   final database backup so rollback has a consistent cutover point.

## Release configuration

A service-specific systemd drop-in can select the release without changing other
services. Replace all example paths with the verified locations:

```ini
[Service]
WorkingDirectory=/srv/price-tracker/releases/COMMIT
EnvironmentFile=/srv/price-tracker/shared/.env
Environment=DATABASE_PATH=/srv/price-tracker/shared/tracker.db
ExecStart=
ExecStart=/srv/price-tracker/releases/COMMIT/.venv/bin/python -m app.bot.bot
```

Reload systemd and start the service. Startup migrates the database transactionally
and creates its own pre-migration backup. Do not point the old application at the
new schema.

## Verification

- Confirm the running working directory and revision match the pushed commit.
- Confirm `active/running`, a stable PID, and zero unexpected restarts.
- Check Telegram `getMe` returns the expected username without logging the token.
- Run the read-only health report:

```sh
python -m app.health --database /srv/price-tracker/shared/tracker.db
```

- Compare counts with the pre-deployment snapshot. Confirm schema version 1,
  integrity `ok`, no foreign-key errors, and recent scheduler attempts.
- Inspect service logs for startup, polling conflicts, migration errors, and
  delivery failures. Unavailable or blocked Amazon pages should have an explicit
  status rather than a fabricated price.
- Use `/start` in the existing bot to get current buttons. Old menus are rejected
  safely. Live user interactions are separate from mocked UI test coverage.

## Rollback

Stop the service, preserve the new database for investigation, restore the
pre-cutover database, and restore the previous service configuration. Reload
systemd and start the previous release. Verify identity and health again.
Restoring the old database discards post-cutover changes; keep both copies and
reconcile any intervening user edits before a later retry.

Do not delete a database, backup, or previous release as part of routine deployment.
