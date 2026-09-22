import argparse
import json
import sqlite3
from pathlib import Path


def inspect(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        result = {
            "schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "integrity": connection.execute("PRAGMA quick_check").fetchone()[0],
            "foreign_key_errors": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        }
        for table in ("catalog", "subscriptions", "folders", "observations"):
            result[table] = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        result["statuses"] = dict(connection.execute("SELECT status,COUNT(*) FROM catalog GROUP BY status"))
        result["deliveries"] = dict(connection.execute("SELECT state,COUNT(*) FROM outbox GROUP BY state"))
        result["latest_attempt"] = connection.execute("SELECT MAX(last_attempt) FROM catalog").fetchone()[0]
        return result
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    result = inspect(args.database)
    print(json.dumps(result, indent=2))
    raise SystemExit(
        0
        if result["integrity"] == "ok" and not result["foreign_key_errors"] and result["schema_version"] == 1
        else 1
    )
