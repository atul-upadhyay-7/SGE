# FastAPI backend image.
#
# Built from the repository root (see the `bms-api` service in
# docker-compose.yml) so later phases can COPY the shared ML code in
# `src/` and the artifacts in `models/` alongside the backend.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/ ./backend

# Shared ML code and artifacts the backend imports at inference time (FR-07):
# `app/ml/estimator.py` resolves the repository root as three levels up from
# itself, so `src/` and `models/` must sit beside `backend/` at /app.
COPY src/ ./src
COPY models/ ./models

WORKDIR /app/backend

EXPOSE 8000

# Liveness for orchestrators/compose: the bare /health route stays open
# even when a viewer key is configured (see app/core/config.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
