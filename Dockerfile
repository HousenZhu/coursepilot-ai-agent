FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/coursepilot-models \
    PYTHONPATH=/app \
    UV_PROJECT_ENVIRONMENT=/opt/coursepilot-venv \
    PATH="/opt/coursepilot-venv/bin:$PATH"
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
ARG INSTALL_DEV=false
RUN pip install uv==0.12.15 \
    && if [ "$INSTALL_DEV" = "true" ]; then uv sync --frozen --no-install-project --extra dev; else uv sync --frozen --no-install-project; fi
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', revision='1110a243fdf4706b3f48f1d95db1a4f5529b4d41')"
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --retries=5 \
  CMD curl --fail http://localhost:8000/health/live || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
