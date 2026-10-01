"""Run one feed through the engine and keep everything it produced."""

import hashlib

from feed_to_matrixify.audit import audit, blockers
from feed_to_matrixify.convert import convert, report_csv, to_csv
from feed_to_matrixify.read import FeedUnreadable, read_feed
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Finding, Run, RunFile

__all__ = ["FeedUnreadable", "find_by_sha", "ingest"]


def find_by_sha(session: Session, sha256: str) -> Run | None:
    return session.scalar(select(Run).where(Run.sha256 == sha256))


def ingest(session: Session, filename: str, raw: bytes) -> tuple[Run, bool]:
    """Return (run, created). The same bytes a second time return the first run."""
    sha256 = hashlib.sha256(raw).hexdigest()
    existing = find_by_sha(session, sha256)
    if existing:
        return existing, False

    if b"\0" in raw:
        # Postgres text cannot hold NUL, and a CSV has no business carrying one.
        raise FeedUnreadable("the file contains NUL bytes: a binary or UTF-16 file, not a CSV")
    feed = read_feed(raw)  # raises FeedUnreadable, which the API turns into a 422
    findings = audit(feed)
    records, skipped = convert(feed, findings)

    run = Run(
        filename=filename[:255],
        sha256=sha256,
        size_bytes=len(raw),
        rows_read=len(feed.rows),
        products=len({r["Handle"] for r in records}),
        variants=len({r["Handle"] for r in records if r["Option1 Value"]}),
        rows_left_out=len(skipped),
        findings_total=len(findings),
        findings_blocking=len(blockers(findings)),
        delimiter=feed.delimiter,
        encoding=feed.encoding,
        decimal=feed.decimal,
    )
    run.findings = [Finding(level=f.level, row=f.row, sku=f.sku, what=f.what, detail=f.detail)
                    for f in findings]
    session.add(run)
    session.flush()
    session.add_all([RunFile(run_id=run.id, kind="matrixify", content=to_csv(records)),
                     RunFile(run_id=run.id, kind="report", content=report_csv(findings))])
    try:
        session.commit()
    except IntegrityError:
        # Two uploads of the same file raced; the other one won and its run is the answer.
        session.rollback()
        winner = find_by_sha(session, sha256)
        if winner is None:
            raise
        return winner, False
    return run, True
