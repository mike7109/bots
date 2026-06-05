# Build context = repo root (so the shared lib is available):
#   docker build -f services/gitlab-notify/Dockerfile -t gitlab-notify .
# For reproducible builds, pin the base image by digest
# (python:3.12-slim@sha256:...); get the current digest with:
#   docker buildx imagetools inspect python:3.12-slim
FROM python:3.12-slim

WORKDIR /app

# tzdata so the scheduler fires at the team's local time (set TZ in the env,
# e.g. TZ=Europe/Moscow). Without it the container is UTC and "09:00" misfires.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Shared library first (changes rarely -> better layer caching).
COPY libs/botkit /app/libs/botkit
RUN pip install --no-cache-dir /app/libs/botkit \
    && pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn[standard]>=0.34,<1"

# Service code (app, cron, normalize, wiring, config, templates).
# This also ships static/ (admin CSS/JS), so the admin panel's assets are baked
# into the image — no separate volume mount needed (they version with the code).
COPY services/gitlab-notify /app/svc
WORKDIR /app/svc

# Create the state dir owned by the runtime user: a fresh named volume mounted
# on /data inherits this ownership, so SQLite (STATE_DB=/data/state.db) is
# writable. Without this the non-root user can't write and the service crashes.
RUN useradd -u 10001 -m appuser && mkdir -p /data && chown 10001:10001 /data
USER 10001

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
