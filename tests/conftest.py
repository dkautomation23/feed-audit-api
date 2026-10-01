"""Every test runs against a real Postgres: a throwaway database, built by the Alembic migrations."""

import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://feedaudit:feedaudit@localhost:5435/feedaudit_test")
API_KEY = "test-key"
UPLOAD_LIMIT = 64 * 1024

# Before the app is imported: settings are read once and cached.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["API_KEY"] = API_KEY
os.environ["MAX_UPLOAD_BYTES"] = str(UPLOAD_LIMIT)

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample" / "supplier-feed.csv"


@pytest.fixture(scope="session", autouse=True)
def database():
    url = make_url(TEST_DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(config, "head")
    yield
    get_engine().dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE runs RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app, headers={"X-API-Key": API_KEY}) as c:
        yield c


@pytest.fixture
def anon():
    with TestClient(app) as c:
        yield c


def small_feed(n: int, rows: int = 2) -> bytes:
    """A distinct, valid feed per n."""
    lines = ["SKU,Title,Price,Stock,EAN,Image"]
    for i in range(rows):
        lines.append(f"S{n}-{i},Product {n} {i},{10 + i}.50,{i},40000{n:04d}{i:03d},https://example.com/{n}-{i}.jpg")
    return ("\n".join(lines) + "\n").encode()


def upload(client, data: bytes, name: str = "feed.csv"):
    return client.post("/runs", files={"file": (name, data, "text/csv")})
