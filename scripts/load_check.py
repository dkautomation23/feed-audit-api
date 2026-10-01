"""Upload 200 synthetic feeds one at a time and time the two calls that matter.

    python scripts/load_check.py

Runs the app in-process (FastAPI's TestClient, one thread) against a fresh
database "feedaudit_load" on the Postgres from docker compose, so the numbers
are the API plus the engine plus Postgres, without network noise, and a rerun
never meets the previous run's files (which would all be fast "already seen"
answers). Prints the result and writes it to docs/load-check.txt, which the
README quotes and check.py compares.
"""

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

LOAD_URL = os.environ.get("LOAD_DATABASE_URL",
                          "postgresql+psycopg://feedaudit:feedaudit@localhost:5435/feedaudit_load")
os.environ["DATABASE_URL"] = LOAD_URL
os.environ["API_KEY"] = "load-check"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from make_feeds import make  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.main import app  # noqa: E402

FEEDS = 200


def percentile(samples: list[float], p: int) -> float:
    return statistics.quantiles(samples, n=100, method="inclusive")[p - 1]


def fresh_database() -> None:
    url = make_url(LOAD_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["database_url"] = LOAD_URL
    command.upgrade(config, "head")


def main() -> int:
    fresh_database()
    files = make(FEEDS, Path(tempfile.mkdtemp()))
    post_ms, findings_ms, run_ids, findings = [], [], [], 0
    with TestClient(app, headers={"X-API-Key": "load-check"}) as client:
        client.get("/health")  # open the connection pool outside the timing
        for path in files:
            data = path.read_bytes()
            start = time.perf_counter()
            response = client.post("/runs", files={"file": (path.name, data, "text/csv")})
            post_ms.append((time.perf_counter() - start) * 1000)
            if response.status_code != 201:
                print(f"{path.name}: {response.status_code} {response.text[:200]}")
                return 1
            run_ids.append(response.json()["id"])
        for run_id in run_ids:
            start = time.perf_counter()
            response = client.get(f"/runs/{run_id}/findings")
            findings_ms.append((time.perf_counter() - start) * 1000)
            findings += len(response.json())
    get_engine().dispose()

    lines = [
        f"feeds uploaded: {len(run_ids)}, findings stored: {findings}",
        f"POST /runs: p50 {percentile(post_ms, 50):.1f} ms, p95 {percentile(post_ms, 95):.1f} ms",
        f"GET /runs/{{id}}/findings: p50 {percentile(findings_ms, 50):.1f} ms, "
        f"p95 {percentile(findings_ms, 95):.1f} ms",
    ]
    report = "\n".join(lines) + "\n"
    print(report, end="")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "load-check.txt").write_text(report, encoding="utf-8", newline="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
