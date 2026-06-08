# Единый прод-образ issue-graph: собираем фронт и отдаём его статикой из бэкенда
# (один origin — без CORS и проблем с httpOnly-cookie). Контекст сборки —
# каталог services/issue-graph.

# ── 1. сборка фронта ──
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build   # → /fe/dist

# ── 2. рантайм: FastAPI + статика фронта ──
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /fe/dist ./static

# данные (конфиг инстанса + SQLite-слой связей) — в /data (том)
ENV STATIC_DIR=/app/static \
    ISSUE_GRAPH_CONFIG=/data/.gl_config.json \
    ISSUE_GRAPH_DB=/data/issue_graph.db
RUN mkdir -p /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
