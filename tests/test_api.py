import asyncio
import re
import subprocess
import sys

import pytest
from conftest import API_KEY, SAMPLE, UPLOAD_LIMIT, small_feed, upload
from sqlalchemy import func, select

from app import service
from app.db import get_engine, get_session
from app.main import app
from app.models import Finding, Run, RunFile


@pytest.fixture(scope="module")
def cli_run(tmp_path_factory):
    """What the engine's own CLI prints and writes for the sample feed."""
    out = tmp_path_factory.mktemp("cli")
    done = subprocess.run([sys.executable, "-m", "feed_to_matrixify.cli", str(SAMPLE), "--out", str(out)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode in (0, 1), done.stderr
    return done.stdout, out


def cli_numbers(stdout: str) -> dict:
    rows = re.search(r": (\d+) rows,", stdout)
    written = re.search(r"\d+ rows -> (\d+) products, (\d+) of them", stdout)
    left = re.search(r"(\d+) row\(s\) left out", stdout)
    found = re.search(r"(\d+) finding\(s\), (\d+) of them blocking", stdout)
    return {"rows_read": int(rows.group(1)), "products": int(written.group(1)), "variants": int(written.group(2)),
            "rows_left_out": int(left.group(1)) if left else 0,
            "findings_total": int(found.group(1)), "findings_blocking": int(found.group(2))}


# --- health and docs -------------------------------------------------------

def test_health_checks_the_database(anon):
    response = anon.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_503_when_the_database_is_gone(anon):
    class Broken:
        def execute(self, *args, **kwargs):
            raise ConnectionError("database is down")

    app.dependency_overrides[get_session] = lambda: Broken()
    try:
        response = anon.get("/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_docs_are_open(anon, path):
    assert anon.get(path).status_code == 200


# --- auth -----------------------------------------------------------------

PROTECTED = ["/runs", "/runs/1", "/runs/1/findings", "/runs/1/matrixify.csv", "/runs/1/report.csv",
             "/stats/findings"]


@pytest.mark.parametrize("path", PROTECTED)
def test_no_key_is_401(anon, path):
    assert anon.get(path).status_code == 401


@pytest.mark.parametrize("path", PROTECTED)
def test_wrong_key_is_401(anon, path):
    assert anon.get(path, headers={"X-API-Key": "test-key-but-wrong"}).status_code == 401


def test_upload_without_key_is_401_and_stores_nothing(anon):
    response = anon.post("/runs", files={"file": ("feed.csv", SAMPLE.read_bytes())})
    assert response.status_code == 401
    with get_engine().connect() as conn:
        assert conn.scalar(select(func.count()).select_from(Run)) == 0


# --- POST /runs -----------------------------------------------------------

def test_sample_feed_gives_the_same_numbers_as_the_cli(client, cli_run):
    stdout, _ = cli_run
    response = upload(client, SAMPLE.read_bytes(), SAMPLE.name)
    assert response.status_code == 201
    body = response.json()
    assert {k: body[k] for k in cli_numbers(stdout)} == cli_numbers(stdout)
    assert (body["delimiter"], body["encoding"], body["decimal"]) == (";", "utf-8-sig", ",")
    assert response.headers["location"] == f"/runs/{body['id']}"
    assert body["already_seen"] is False


def test_findings_summary_is_the_cli_summary(client, cli_run):
    stdout, _ = cli_run
    printed = dict((what, int(n)) for n, what in re.findall(r"^\s+(\d+)  (.+)$", stdout, re.M))
    assert upload(client, SAMPLE.read_bytes()).json()["findings_by_what"] == printed


def test_the_same_file_twice_is_the_same_run(client):
    first = upload(client, SAMPLE.read_bytes(), "monday.csv")
    second = upload(client, SAMPLE.read_bytes(), "tuesday.csv")
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["already_seen"] is True
    assert second.json()["filename"] == "monday.csv"
    assert client.get("/runs").json()["items"].__len__() == 1


def test_every_finding_and_both_files_are_stored(client):
    run = upload(client, SAMPLE.read_bytes()).json()
    with get_engine().connect() as conn:
        assert conn.scalar(select(func.count()).select_from(Finding)) == run["findings_total"]
        kinds = conn.scalars(select(RunFile.kind).order_by(RunFile.kind)).all()
    assert kinds == ["matrixify", "report"]


def test_a_feed_without_the_required_columns_is_422(client):
    response = upload(client, b"Name,Code\nGermany,DE\n", "countries.csv")
    assert response.status_code == 422
    assert response.json()["detail"].startswith("FeedUnreadable: cannot identify the column holding")


def test_an_empty_file_is_422(client):
    response = upload(client, b"", "empty.csv")
    assert response.status_code == 422
    assert "FeedUnreadable" in response.json()["detail"]


def test_a_binary_file_is_422_not_a_crash(client):
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))
    response = upload(client, png, "photo.png")
    assert response.status_code == 422
    assert "NUL bytes" in response.json()["detail"]


def test_a_file_one_byte_over_the_limit_is_413_and_not_stored(client):
    feed = small_feed(1)
    response = upload(client, feed + b"#" * (UPLOAD_LIMIT + 1 - len(feed)))
    assert response.status_code == 413
    assert client.get("/runs").json()["items"] == []


def test_a_file_exactly_at_the_limit_is_read(client):
    feed = small_feed(1)
    padded = feed + b"#" * (UPLOAD_LIMIT - len(feed))
    assert len(padded) == UPLOAD_LIMIT
    assert upload(client, padded).status_code == 201


class Body:
    """A multipart body sent chunk by chunk, counting how much the server pulled."""

    def __init__(self, megabytes: int):
        self.sent = 0
        self.megabytes = megabytes
        self.boundary = "bound"

    def __iter__(self):
        yield f'--{self.boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.csv"\r\n\r\n'.encode()
        for _ in range(self.megabytes * 16):
            self.sent += 1
            yield b"#" * 65536
        yield f"\r\n--{self.boundary}--\r\n".encode()

    def headers(self, key: str | None = API_KEY, length: bool = False) -> dict:
        headers = {"Content-Type": f"multipart/form-data; boundary={self.boundary}"}
        if key:
            headers["X-API-Key"] = key
        if length:
            headers["Content-Length"] = str(self.megabytes * 1024 * 1024 + 200)
        return headers


def test_an_upload_declared_over_the_limit_is_413_before_its_body_is_read(anon):
    body = Body(megabytes=200)
    response = anon.post("/runs", content=body, headers=body.headers(length=True))
    assert response.status_code == 413
    assert body.sent < 16, f"server read {body.sent} of {body.megabytes * 16} chunks before refusing"


def test_an_undeclared_upload_is_cut_off_once_it_passes_the_limit():
    """Chunked, no Content-Length. Called as ASGI directly: the test client
    buffers a streamed body before the app sees it, so it cannot show this."""
    body = Body(megabytes=200)
    chunks = iter(body)
    sent: list = []

    async def receive():
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/runs", "raw_path": b"/runs", "query_string": b"",
             "root_path": "", "server": ("test", 80), "client": ("test", 1),
             "headers": [(k.lower().encode(), v.encode()) for k, v in body.headers().items()]}
    asyncio.run(app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    # 64 KiB limit plus multipart overhead: refused on the second chunk, not the 3200th.
    assert body.sent < 16, f"server read {body.sent} of {body.megabytes * 16} chunks before refusing"
    with get_engine().connect() as conn:
        assert conn.scalar(select(func.count()).select_from(Run)) == 0


@pytest.mark.parametrize("key", [None, "test-key-but-wrong"])
def test_an_upload_with_no_or_a_wrong_key_is_401_before_its_body_is_read(anon, key):
    body = Body(megabytes=200)
    response = anon.post("/runs", content=body, headers=body.headers(key=key, length=True))
    assert response.status_code == 401
    assert body.sent < 16


@pytest.mark.parametrize("path", ["/runs/2147483648", "/runs/99999999999999999999/findings",
                                  "/runs/2147483648/matrixify.csv", "/runs/0", "/runs?after=2147483648"])
def test_an_id_postgres_cannot_hold_is_422_not_500(client, path):
    assert client.get(path).status_code == 422


def test_the_largest_id_postgres_can_hold_is_a_plain_404(client):
    assert client.get("/runs/2147483647").status_code == 404


def test_an_upload_without_a_file_is_422(client):
    assert client.post("/runs").status_code == 422


def test_a_header_only_feed_is_a_run_with_no_rows(client):
    body = upload(client, b"SKU,Title,Price\n").json()
    assert (body["rows_read"], body["products"], body["findings_total"]) == (0, 0, 0)


def test_a_windows_export_is_read(client):
    feed = "Artikelnummer|Bezeichnung|Preis\nK-1|Käsemesser|12,50\n".encode("cp1252")
    body = upload(client, feed).json()
    assert (body["delimiter"], body["encoding"], body["decimal"]) == ("|", "cp1252", ",")


def test_a_lost_race_returns_the_winning_run(client, monkeypatch):
    """Two uploads of one file at once: the loser hits the UNIQUE(sha256) and answers with the winner."""
    winner = upload(client, SAMPLE.read_bytes()).json()
    real = service.find_by_sha
    calls = []

    def stale_then_real(session, sha256):
        calls.append(sha256)
        return None if len(calls) == 1 else real(session, sha256)

    monkeypatch.setattr(service, "find_by_sha", stale_then_real)
    again = upload(client, SAMPLE.read_bytes())
    assert again.status_code == 200
    assert again.json()["id"] == winner["id"]


# --- reading ---------------------------------------------------------------

@pytest.mark.parametrize(("endpoint", "written"), [("matrixify.csv", "matrixify-import.csv"),
                                                   ("report.csv", "audit-report.csv")])
def test_csv_downloads_are_byte_for_byte_the_cli_files(client, cli_run, endpoint, written):
    _, out = cli_run
    run_id = upload(client, SAMPLE.read_bytes()).json()["id"]
    response = client.get(f"/runs/{run_id}/{endpoint}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content == (out / written).read_bytes()


def test_read_one_run(client):
    created = upload(client, SAMPLE.read_bytes()).json()
    fetched = client.get(f"/runs/{created['id']}").json()
    assert fetched["sha256"] == created["sha256"]
    assert fetched["products"] == created["products"]


@pytest.mark.parametrize("path", ["/runs/999", "/runs/999/findings", "/runs/999/matrixify.csv",
                                  "/runs/999/report.csv"])
def test_an_unknown_run_is_404(client, path):
    assert client.get(path).status_code == 404


def test_findings_filter_by_level_and_what(client):
    run_id = upload(client, SAMPLE.read_bytes()).json()["id"]
    every = client.get(f"/runs/{run_id}/findings").json()
    blockers = client.get(f"/runs/{run_id}/findings", params={"level": "blocker"}).json()
    no_image = client.get(f"/runs/{run_id}/findings", params={"what": "no image"}).json()
    assert len(every) == 11
    assert [f["what"] for f in blockers] == ["no SKU"]
    assert len(no_image) == 2 and all(f["what"] == "no image" for f in no_image)


def test_paging_through_every_page_returns_every_run_exactly_once(client):
    ids = [upload(client, small_feed(n)).json()["id"] for n in range(11)]
    seen, after, pages = [], 0, 0
    while True:
        page = client.get("/runs", params={"after": after, "limit": 3}).json()
        seen += [r["id"] for r in page["items"]]
        pages += 1
        if page["next_cursor"] is None:
            break
        after = page["next_cursor"]
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen))
    assert pages == 4


def test_a_page_that_ends_exactly_on_the_last_run_has_no_next_cursor(client):
    for n in range(3):
        upload(client, small_feed(n))
    assert client.get("/runs", params={"limit": 3}).json()["next_cursor"] is None


@pytest.mark.parametrize("limit", [0, 101])
def test_page_size_outside_1_to_100_is_422(client, limit):
    assert client.get("/runs", params={"limit": limit}).status_code == 422


def test_stats_count_findings_across_runs(client):
    upload(client, SAMPLE.read_bytes())
    upload(client, b"SKU,Title,Price\nA-1,Knife,0\nA-2,Fork,0\n")
    stats = client.get("/stats/findings").json()
    counts = [s["findings"] for s in stats]
    assert counts == sorted(counts, reverse=True)
    by_what = {(s["what"], s["level"]): s for s in stats}
    with get_engine().connect() as conn:
        total = conn.scalar(select(func.count()).select_from(Finding))
    assert sum(s["findings"] for s in stats) == total
    assert max(s["runs"] for s in by_what.values()) == 2
