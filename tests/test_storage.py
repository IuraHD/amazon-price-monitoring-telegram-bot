import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.core import db, folders, products
from app.utils.fetch import Offer, ProductLink

LINK = ProductLink("B012345678", "https://www.amazon.co.uk/dp/B012345678")


def test_independent_subscriptions_and_ownership():
    one, _ = products.add_product(111, LINK)
    two, _ = products.add_product(222, LINK)
    assert one != two
    assert products.get_product(222, one) is None
    assert products.get_product(111, one)["product_id"] == products.get_product(222, two)["product_id"]
    with pytest.raises(ValueError):
        products.set_alerts(222, one, enabled=False)
    products.remove_product(222, one)
    assert products.get_product(111, one)


def test_duplicate_preserves_folder_and_preferences():
    f = folders.create_folder(111, "First")
    sid, created = products.add_product(111, LINK, f)
    products.set_alerts(111, sid, mode="target", target=999)
    again, created = products.add_product(111, LINK)
    assert again == sid and not created
    row = products.get_product(111, sid)
    assert row["folder_id"] == f and row["target_pence"] == 999


def test_cascades_and_shared_history():
    f = folders.create_folder(111, "First")
    sid, _ = products.add_product(111, LINK, f)
    other, _ = products.add_product(222, LINK)
    pid = products.get_product(111, sid)["product_id"]
    products.record_offer(pid, Offer("ok", 1000), 10)
    folders.delete_folder(222, f)
    assert folders.get_folder(111, f)
    folders.delete_folder(111, f)
    assert products.get_product(111, sid)["folder_id"] is None
    products.remove_product(111, sid)
    assert products.history(222, other)
    products.remove_product(222, other)
    assert not db.fetch_all("SELECT * FROM catalog")
    assert not db.fetch_all("SELECT * FROM observations")


def test_folder_move_requires_ownership():
    sid, _ = products.add_product(111, LINK)
    foreign = folders.create_folder(222, "Private")
    with pytest.raises(ValueError):
        products.move_product(111, sid, foreign)
    with pytest.raises(ValueError):
        products.move_product(222, sid, None)
    with pytest.raises(ValueError):
        products.add_product(111, LINK, foreign)


def test_empty_and_compound_folder_input():
    with pytest.raises(ValueError):
        folders.create_folder(111, "   ")
    for value in ["🇬🇧", "👩🏽‍💻", "📁"]:
        assert folders.clean_emoji(value) == value
    with pytest.raises(ValueError):
        folders.clean_emoji("hello")
    with pytest.raises(ValueError):
        folders.clean_emoji("📁📁")


def test_exact_penny_alert_and_atomic_observation():
    sid, _ = products.add_product(111, LINK)
    pid = products.get_product(111, sid)["product_id"]
    products.record_offer(pid, Offer("ok", 1000), 10)
    products.record_offer(pid, Offer("ok", 1001), 10)
    assert len(db.fetch_all("SELECT * FROM outbox")) == 1
    assert len(products.history(111, sid)) == 2
    assert products.get_product(111, sid)["price_pence"] == 1001


def test_target_drop_percentage_and_mute():
    sid, _ = products.add_product(111, LINK)
    pid = products.get_product(111, sid)["product_id"]
    products.set_alerts(111, sid, mode="target", target=1000)
    products.record_offer(pid, Offer("ok", 1200), 10)
    products.record_offer(pid, Offer("ok", 1000), 10)
    assert len(db.fetch_all("SELECT * FROM outbox")) == 1
    products.record_offer(pid, Offer("ok", 900), 10)
    assert len(db.fetch_all("SELECT * FROM outbox")) == 1
    products.set_alerts(111, sid, mode="drop", percent=10)
    products.record_offer(pid, Offer("ok", 850), 10)
    assert not db.fetch_all("SELECT * FROM outbox")
    products.record_offer(pid, Offer("ok", 700), 10)
    assert len(db.fetch_all("SELECT * FROM outbox")) == 1
    products.set_alerts(111, sid, enabled=False)
    products.record_offer(pid, Offer("ok", 500), 10)
    assert not db.fetch_all("SELECT * FROM outbox")


def test_failures_retain_price_and_last_success():
    sid, _ = products.add_product(111, LINK)
    pid = products.get_product(111, sid)["product_id"]
    products.record_offer(pid, Offer("ok", 1000, "Title"), 10)
    before = products.get_product(111, sid)
    products.record_offer(pid, Offer("blocked", detail="Captcha"), 10)
    after = products.get_product(111, sid)
    assert after["price_pence"] == 1000 and after["last_success"] == before["last_success"]
    assert after["status"] == "blocked" and after["detail"] == "Captcha"
    assert len(products.history(111, sid)) == 1


def test_stock_only_alert_requires_explicit_transition():
    sid, _ = products.add_product(111, LINK)
    pid = products.get_product(111, sid)["product_id"]
    products.set_alerts(111, sid, stock=True)
    products.record_offer(pid, Offer("ok", 1000, availability="unknown"), 10)
    products.record_offer(pid, Offer("unavailable", availability="unavailable"), 10)
    products.record_offer(pid, Offer("blocked"), 10)
    products.record_offer(pid, Offer("ok", 1000, availability="available"), 10)
    assert "back in stock" in db.fetch_one("SELECT * FROM outbox")["text"]


def test_history_preserves_endpoints_extrema_and_limit():
    sid, _ = products.add_product(111, LINK)
    pid = products.get_product(111, sid)["product_id"]
    with db.connection() as conn:
        for i in range(399):
            price = 1 if i == 222 else 9000 if i == 201 else 1000 + i
            ts = (datetime.now(timezone.utc) - timedelta(seconds=400 - i)).isoformat()
            conn.execute(
                "INSERT INTO observations(product_id,ts,price_pence) VALUES(?,?,?)", (pid, ts, price)
            )
    rows = products.history(111, sid, 90, limit=200)
    assert len(rows) <= 200 and rows[0]["price_pence"] == 1000 and rows[-1]["price_pence"] == 1398
    assert min(r["price_pence"] for r in rows) == 1 and max(r["price_pence"] for r in rows) == 9000
    assert b"timestamp_utc,price_gbp,source" in products.export_csv(111, sid)
    with pytest.raises(ValueError):
        products.export_csv(222, sid)


def test_pagination_search_and_sort():
    for i in range(12):
        link = ProductLink(f"B{i:09}", f"https://www.amazon.co.uk/dp/B{i:09}")
        sid, _ = products.add_product(111, link)
        pid = products.get_product(111, sid)["product_id"]
        products.record_offer(pid, Offer("ok", 1000 + i, f"Product {i}"), 10)
    rows, total, page = products.list_products(111, page=1, sort="price")
    assert total == 12 and len(rows) == 4 and page == 1
    assert rows[0]["price_pence"] == 1008
    assert products.list_products(111, query="Product 11")[1] == 1
    assert products.list_products(222)[1] == 0


def test_quiet_hours_timezone_and_daytime():
    products.set_quiet_hours(111, 22, 8, "Europe/London")
    pref = db.fetch_one("SELECT * FROM preferences WHERE chat_id=111")
    assert products.is_quiet(pref, datetime(2026, 7, 1, 21, tzinfo=timezone.utc))
    assert not products.is_quiet(pref, datetime(2026, 7, 1, 7, tzinfo=timezone.utc))
    products.set_quiet_hours(111, 9, 17, "UTC")
    pref = db.fetch_one("SELECT * FROM preferences WHERE chat_id=111")
    assert products.is_quiet(pref, datetime(2026, 7, 1, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize("value", ["NaN", "-1", "0", "1.001", "infinity", "hello"])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        products.parse_amount(value)


def test_legacy_migration_backups_repairs_and_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE folders(id INTEGER PRIMARY KEY,name TEXT,emoji TEXT,chat_id INTEGER);
        CREATE TABLE products(id INTEGER PRIMARY KEY,asin TEXT UNIQUE,url TEXT,alerts_enabled INTEGER,last_price REAL,chat_id INTEGER,folder_id INTEGER,created_ts TEXT,title TEXT);
        CREATE TABLE price_history(id INTEGER PRIMARY KEY,product_id INTEGER,ts TEXT,price REAL);
        INSERT INTO folders VALUES(1,'Other','x',222);
        INSERT INTO products VALUES(7,'B012345678','https://www.amazon.co.uk/dp/B012345678',0,10.01,111,1,'2025-01-01','Legacy');
        INSERT INTO price_history VALUES(1,7,'2025-01-01T12:00:00',10.01);
        INSERT INTO price_history VALUES(2,999,'2025-01-01T12:00:00',9.99);
        """)
    backup = db.init_db(path)
    assert backup and backup.exists()
    row = products.get_product(111, 7)
    assert row["price_pence"] == 1001 and row["folder_id"] is None and row["alerts_enabled"] == 0
    assert row["status"] == "unverified" and row["last_success"] is None
    assert len(db.fetch_all("SELECT * FROM observations")) == 1
    with sqlite3.connect(backup) as c:
        assert c.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 2
    assert db.init_db(path) is None
    with db.connection() as c:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_totals_show_unknown_and_failed_prices():
    sid, _ = products.add_product(111, LINK)
    products.add_product(111, ProductLink("B012345679", "https://www.amazon.co.uk/dp/B012345679"))
    pid = products.get_product(111, sid)["product_id"]
    products.record_offer(pid, Offer("ok", 1234), 10)
    products.record_offer(pid, Offer("blocked"), 10)
    assert products.list_summary(111) == {"total_pence": 1234, "unknown": 1, "needs_check": 2}


def test_readonly_health_report(database):
    from app.health import inspect

    result = inspect(database)
    assert result["integrity"] == "ok" and result["schema_version"] == 1
    assert result["foreign_key_errors"] == 0
