FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Install uv ───────────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Install dependencies (cached layer) ─────────────────────────
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Copy application code ───────────────────────────────────────
COPY src/ src/

# ── Expose API port ─────────────────────────────────────────────
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
