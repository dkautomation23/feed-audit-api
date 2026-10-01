"""HTTP API over the feed-to-matrixify engine."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response, UploadFile, status
from fastapi.security import APIKeyHeader
from feed_to_matrixify.audit import summarise
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import service
from app.config import get_settings
from app.db import get_session
from app.models import Finding, Run, RunFile
from app.schemas import FindingOut, FindingStat, Health, RunCreated, RunOut, RunPage

SessionDep = Annotated[Session, Depends(get_session)]
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: Annotated[str | None, Depends(api_key_header)]) -> None:
    expected = get_settings().api_key.encode()
    if key is None or not hmac.compare_digest(key.encode(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or wrong X-API-Key",
                            headers={"WWW-Authenticate": "API-Key"})


app = FastAPI(title="feed-audit-api", version="0.1.0",
              description="Upload a supplier feed, get a Shopify (Matrixify) import and an audit of "
                          "what is wrong with the feed. Every run is kept in Postgres.")
api = APIRouter(dependencies=[Depends(require_api_key)])


@app.get("/health", response_model=Health, tags=["health"])
def health(session: SessionDep, response: Response) -> Health:
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any database failure means not healthy
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="degraded", database="unreachable")
    return Health(status="ok", database="ok")


def get_run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no run {run_id}")
    return run


@api.post("/runs", response_model=RunCreated, status_code=status.HTTP_201_CREATED, tags=["runs"],
          responses={200: {"model": RunCreated, "description": "this file was uploaded before"},
                     413: {"description": "file over MAX_UPLOAD_BYTES"},
                     422: {"description": "FeedUnreadable: the engine could not read the feed"}})
def create_run(file: UploadFile, session: SessionDep, response: Response) -> RunCreated:
    limit = get_settings().max_upload_bytes
    raw = file.file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"file is over the {limit} byte limit")
    try:
        run, created = service.ingest(session, file.filename or "feed.csv", raw)
    except service.FeedUnreadable as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"FeedUnreadable: {error}") from None
    if not created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/runs/{run.id}"
    return RunCreated(**RunOut.model_validate(run).model_dump(), already_seen=not created,
                      findings_by_what=summarise(run.findings))


@api.get("/runs", response_model=RunPage, tags=["runs"])
def list_runs(session: SessionDep,
              after: Annotated[int, Query(ge=0, description="id of the last run on the previous page")] = 0,
              limit: Annotated[int, Query(ge=1, le=100)] = 50) -> RunPage:
    runs = session.scalars(select(Run).where(Run.id > after).order_by(Run.id).limit(limit + 1)).all()
    page = runs[:limit]
    return RunPage(items=[RunOut.model_validate(r) for r in page],
                   next_cursor=page[-1].id if len(runs) > limit else None)


@api.get("/runs/{run_id}", response_model=RunOut, tags=["runs"])
def read_run(run_id: int, session: SessionDep) -> Run:
    return get_run(session, run_id)


@api.get("/runs/{run_id}/findings", response_model=list[FindingOut], tags=["runs"])
def read_findings(run_id: int, session: SessionDep,
                  level: str | None = None, what: str | None = None) -> list[Finding]:
    get_run(session, run_id)
    query = select(Finding).where(Finding.run_id == run_id).order_by(Finding.id)
    if level:
        query = query.where(Finding.level == level)
    if what:
        query = query.where(Finding.what == what)
    return list(session.scalars(query))


def csv_file(session: Session, run_id: int, kind: str, name: str) -> Response:
    get_run(session, run_id)
    content = session.scalar(select(RunFile.content).where(RunFile.run_id == run_id, RunFile.kind == kind))
    return Response(content=(content or "").encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="run-{run_id}-{name}"'})


@api.get("/runs/{run_id}/matrixify.csv", tags=["runs"], response_class=Response,
         responses={200: {"content": {"text/csv": {}}}})
def matrixify_csv(run_id: int, session: SessionDep) -> Response:
    return csv_file(session, run_id, "matrixify", "matrixify-import.csv")


@api.get("/runs/{run_id}/report.csv", tags=["runs"], response_class=Response,
         responses={200: {"content": {"text/csv": {}}}})
def report_csv(run_id: int, session: SessionDep) -> Response:
    return csv_file(session, run_id, "report", "audit-report.csv")


@api.get("/stats/findings", response_model=list[FindingStat], tags=["stats"])
def finding_stats(session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[FindingStat]:
    """The problems suppliers send most often, across every run."""
    rows = session.execute(
        select(Finding.what, Finding.level, func.count().label("findings"),
               func.count(func.distinct(Finding.run_id)).label("runs"))
        .group_by(Finding.what, Finding.level)
        .order_by(func.count().desc(), Finding.what)
        .limit(limit)).all()
    return [FindingStat(what=r.what, level=r.level, findings=r.findings, runs=r.runs) for r in rows]


app.include_router(api)
