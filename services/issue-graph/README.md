# Issue Graph

Интерактивная визуализация GitLab issue в виде графа: связи между задачами,
создание issue и связей прямо из графа, планирование спринтов. Работает на
**любой подписке** — фичи инстанса (epics, blocking-связи, iterations)
определяются автоматически, на Free доступны relates_to-связи и milestones,
на Premium+ подключаются blocking-связи и эпики (graceful degradation).

## Архитектура

```
React + React Flow (5173)  ──/api──►  FastAPI proxy (8000)  ──REST──►  GitLab
```

Токен GitLab живёт только на бэкенде; фронт ходит исключительно в свой прокси
(решает проблемы CORS и утечки токена в браузер).

- `backend/` — FastAPI: детект возможностей, сборка графа, write-эндпоинты.
- `frontend/` — Vite + React Flow + dagre.
- `seed/` — наполнение тестового GitLab данными.

## Возможности

**Фаза 1 — визуализация:** иерархическая раскладка зависимостей (dagre),
карточки задач (метки, спринт, исполнитель, статус), пан/зум, миникарта,
детали по клику, ссылка в GitLab.

**Фаза 2 — навигация:** выбор области (группа/проект), фильтры по
статусу/спринту/метке/исполнителю, поиск, подсветка соседей выбранной ноды.

**Фаза 3 — редактирование:** создание issue из формы, создание связей
перетаскиванием между нодами, смена спринта/закрытие задачи, режим
«Спринты» — раскладка по колонкам-milestones для планирования.

## Запуск

### Вариант А — локально (dev)

Нужен запущенный GitLab и PAT со scope `api`.

```bash
# 1) бэкенд
cd backend
GITLAB_URL=http://localhost:8929 GITLAB_TOKEN=<pat> \
  uvicorn app.main:app --reload --port 8000

# 2) фронт
cd frontend
npm install
npm run dev          # http://localhost:5173
```

### Вариант Б — docker-compose

```bash
cp .env.example .env   # впишите GITLAB_URL и GITLAB_TOKEN
docker compose up --build
```

### Наполнить тестовый GitLab данными

```bash
cd seed
GITLAB_URL=http://localhost:8929 GITLAB_TOKEN=<pat> python seed.py
```

Создаёт группу `graphlab`, 3 проекта, пользователей, метки, milestones и
связанные issue.

## API бэкенда

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/capabilities` | возможности инстанса (tier, epics, blocking…) |
| GET | `/api/namespaces` | группы и проекты для выбора области |
| GET | `/api/graph?group=…\|project=…` | граф `{nodes, edges, meta}` |
| GET | `/api/scope-meta?group=…\|project=…` | milestones/метки/участники/проекты |
| POST | `/api/issues` | создать issue |
| POST | `/api/links` | создать связь (blocks→relates_to фолбэк на Free) |
| PATCH | `/api/issues/{pid}/{iid}` | сменить спринт / закрыть-открыть |

Область можно задавать числовым `id` либо `full_path` (`group=graphlab`).
