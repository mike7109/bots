# bots

Монорепо для вебхук-ботов и автоматизаций. Общий код — один раз в `libs/botkit`,
каждый бот — самостоятельно деплоящийся контейнер в `services/`.

```
bots/
├── libs/
│   └── botkit/                 общая библиотека (uv workspace)
│       └── botkit/
│           ├── matrix.py       отправка в Matrix (body + formatted_body, пинги)
│           ├── gitlab.py       GitLab REST + GraphQL клиент
│           ├── config.py       YAML + env
│           ├── identity.py     gitlab-логин → matrix/email/имя
│           ├── webhook.py      verify X-Gitlab-Token
│           └── notify/         движок: event, engine (rules), render (Jinja), transports/
│
├── services/                   по папке на бота (имя = система-функция)
│   ├── gitlab-notify/          GitLab → Matrix уведомления (webhook + cron)
│   └── gitlab-expand-tasks/    /expand-tasks: Task → Issue
│
├── deploy/                     docker-compose со всем стеком (на сервере GitLab)
├── .github/workflows/build.yml CI: собирает только изменённый сервис → GHCR
└── pyproject.toml              uv workspace
```

## Принципы

- **Общее — в `botkit`.** Matrix/GitLab клиенты, identity-маппинг, движок
  уведомлений. Поправил тут → починилось у всех ботов.
- **Боты изолированы.** Свой образ `ghcr.io/mike7109/<service>`, свой вебхук,
  свой конфиг. Деплоятся и падают независимо.
- **Поведение — в конфиге, не в коде.** У `gitlab-notify` внешний вид в
  `templates/`, правила/маршруты в `config.yaml` — без пересборки.
- **Каналы сменные.** Matrix сейчас; email/телеграм — добавить транспорт в
  `botkit.notify.transports`, код ботов не трогается.

## Разработка (uv workspace)

```bash
uv sync                    # поставит botkit + зависимости всех сервисов
uv run -- uvicorn app:app --reload --port 8080   # из папки services/<bot>/
```
> `uv` не установлен на машине — `curl -LsSf https://astral.sh/uv/install.sh | sh`.
> Без uv можно по-старому: `pip install -e libs/botkit` + `pip install` зависимостей сервиса.

## Сборка / реджистри

CI (GitHub Actions) на push в `main`/тег `v*` собирает **только изменённый**
сервис и пушит в GHCR: `ghcr.io/mike7109/<service>`. Изменение в `libs/**`
пересобирает всех зависимых. Образы по умолчанию приватные — после первого
билда сделать публичными (Packages → Package settings → Visibility → Public).

## Деплой и запуск

Всё крутится на сервере с GitLab, образы из GHCR, порты только на `127.0.0.1`,
вебхуки GitLab — на соответствующий localhost-порт.

| Документ | О чём |
|---|---|
| [`deploy/README.md`](deploy/README.md) | развернуть **весь стек** на сервере (предусловия, порты, cron, обновление) |
| [`services/gitlab-notify/README.md`](services/gitlab-notify/README.md) | запуск/настройка бота уведомлений + **откуда брать каждую настройку** |
| [`services/gitlab-expand-tasks/README.md`](services/gitlab-expand-tasks/README.md) | запуск/настройка `/expand-tasks` + **откуда брать каждую настройку** |

## Добавить нового бота

1. `services/<system>-<function>/` — `app.py`, `pyproject.toml` (`botkit` в deps,
   `[tool.uv] package = false`), `Dockerfile` (контекст = корень), `config.yaml`,
   `Dockerfile`, `README.md`.
2. Добавить сервис в matrix CI (`.github/workflows/build.yml`) и в `deploy/`.
3. Переиспользовать `botkit` — не плодить копии Matrix/GitLab клиентов.
