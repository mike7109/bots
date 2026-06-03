# Build context = repo root (monorepo convention):
#   docker build -f services/gitlab-expand-tasks/Dockerfile -t gitlab-expand-tasks .
FROM python:3.12-slim

WORKDIR /app

COPY services/gitlab-expand-tasks/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/gitlab-expand-tasks/app.py services/gitlab-expand-tasks/expand.py ./

RUN useradd -u 10001 -m appuser
USER 10001

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
