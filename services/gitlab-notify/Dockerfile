# Build context = repo root (so the shared lib is available):
#   docker build -f services/gitlab-notify/Dockerfile -t gitlab-notify .
FROM python:3.12-slim

WORKDIR /app

# Shared library first (changes rarely -> better layer caching).
COPY libs/botkit /app/libs/botkit
RUN pip install --no-cache-dir /app/libs/botkit \
    && pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.34"

# Service code (app, cron, normalize, wiring, config, templates).
COPY services/gitlab-notify /app/svc
WORKDIR /app/svc

# Create the state dir owned by the runtime user: a fresh named volume mounted
# on /data inherits this ownership, so SQLite (STATE_DB=/data/state.db) is
# writable. Without this the non-root user can't write and the service crashes.
RUN useradd -u 10001 -m appuser && mkdir -p /data && chown 10001:10001 /data
USER 10001

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
