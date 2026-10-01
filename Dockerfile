# syntax=docker/dockerfile:1

# Build stage: every dependency goes into a virtualenv that the runtime stage copies whole.
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
# The engine comes from the "engine" build context (see docker-compose.yml), at the same
# commit requirements.txt pins, so the image never needs git or GitHub credentials.
RUN grep -v '^feed-to-matrixify' requirements.txt > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt
COPY --from=engine pyproject.toml README.md /engine/
COPY --from=engine feed_to_matrixify /engine/feed_to_matrixify
RUN pip install /engine

# Runtime stage: no compiler, no pip cache, not root.
FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app
COPY --from=build /opt/venv /opt/venv
WORKDIR /srv
COPY alembic.ini .
COPY migrations migrations
COPY app app
USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
# Migrations run before the server takes traffic; a failed migration stops the container.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
