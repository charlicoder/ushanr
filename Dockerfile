# ─────────────────────────────────────────────────────────────────────────
# ushanr — Production Dockerfile
# Multi-stage build: deps → runtime
# ─────────────────────────────────────────────────────────────────────────

# ── Stage 1: Dependency resolver ─────────────────────────────────────────
FROM python:3.14-slim AS deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime image ───────────────────────────────────────────────
FROM python:3.14-slim AS runtime

RUN groupadd -r ushapp && useradd -r -g ushapp ushapp

WORKDIR /app

COPY --from=deps /install /usr/local

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./alembic.ini
COPY pyproject.toml ./
COPY requirements.txt ./

RUN chown -R ushapp:ushapp /app

USER ushapp

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8007/api/v1/health/')"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8007

CMD ["uvicorn", "app.main:app", \
    "--host", "0.0.0.0", \
    "--port", "8007", \
    "--workers", "2", \
    "--loop", "uvloop", \
    "--http", "httptools", \
    "--proxy-headers", \
    "--forwarded-allow-ips", "*"]
