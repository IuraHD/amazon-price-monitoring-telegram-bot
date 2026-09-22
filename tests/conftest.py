import pytest
from app.core import db


@pytest.fixture(autouse=True)
def database(tmp_path):
    db.init_db(tmp_path / "tracker.db")
    return db.DB_PATH
