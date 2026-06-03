# gitlab-expand-tasks

По комментарию `/expand-tasks` в issue берёт её дочерние **Task**, конвертирует
каждый в **Issue**, вешает label и связывает (relates to) с исходной issue.
Отчёт постит комментарием обратно в ту же issue.

```
/expand-tasks            # dry-run: план (сколько тасков развернётся)
/expand-tasks confirm    # выполнить + отчёт об успехах/ошибках
```

Двухфазно намеренно (операция необратима). Запускать может только участник с
access level ≥ `MIN_ROLE` (по умолчанию Developer). Повторный `confirm`
идемпотентен — уже развёрнутые (не-Task) отфильтровываются.

> Переехало из `mike7109/gitlab-webhook-expand-tasks` в монорепо без изменений
> логики. Пока использует **свои** хелперы (не `botkit`) — это в TODO.

```
GitLab note "/expand-tasks" ─webhook─▶ app.py ─▶ expand.py (GraphQL) ─▶ отчёт-коммент
```

---

## Откуда брать каждую настройку

Все настройки — в `.env` (из `.env.example`):

| Переменная | Что это | Где взять |
|---|---|---|
| `GITLAB_URL` | адрес GitLab | `https://git.fakspro.ru` |
| `GITLAB_TOKEN` | PAT бот-аккаунта | GitLab под ботом → **User settings → Access Tokens** → scope `api` (нужны мутации). Роль бота в группе ≥ Developer. |
| `WEBHOOK_SECRET` | секрет вебхука | сгенерь: `openssl rand -hex 24`. То же значение → Secret token вебхука. |
| `BOT_USER_ID` | numeric id бот-аккаунта | команда ниже. Нужен для анти-цикла: бот игнорит свои же отчёт-комменты. |
| `MIN_ROLE` | мин. право автора команды | `30` = Developer, `40` = Maintainer |
| `TASK_LABEL` | какой label вешать | scoped-label, по умолч. `type::task`. **Скрипт лейбл не создаёт** — заведи заранее (Manage → Labels в группе). |
| `LOG_LEVEL` | `INFO` / `DEBUG` | по вкусу |

Узнать numeric id бота:
```bash
GL="личный_токен_или_токен_бота"
curl -s --header "PRIVATE-TOKEN: $GL" \
  "https://git.fakspro.ru/api/v4/users?username=gitlab-bot" | jq '.[].id'
# -> BOT_USER_ID
```

> **Отдельный бот-аккаунт обязателен**: отчёт-коммент не должен триггерить сам
> себя. Можно тот же сервис-аккаунт, что у других ботов, если у него роль ≥ Developer.

---

## Запуск

### Локально (отладка)
```bash
cd services/gitlab-expand-tasks
cp .env.example .env            # заполнить
pip install -r requirements.txt
set -a; source .env; set +a
uvicorn app:app --reload --port 8080
curl -s localhost:8080/healthz  # {"status":"ok"}
```

### Docker (одиночно)
```bash
cd services/gitlab-expand-tasks
cp .env.example .env            # заполнить
# образ собирается из КОРНЯ монорепо (единый контекст со всеми сервисами):
docker build -f Dockerfile -t gitlab-expand-tasks ../..
IMAGE=gitlab-expand-tasks docker compose up -d
curl -s localhost:8080/healthz
```

### Прод
Через общий стек — см. **`../../deploy/README.md`** (образ из GHCR).

---

## Подключить к GitLab

1. Завести label `type::task` (или свой `TASK_LABEL`) в группе FAKSPRO
   (Manage → Labels). Скрипт его не создаёт.
2. Группа FAKSPRO → **Settings → Webhooks → Add new webhook**:
   - **URL:** `http://127.0.0.1:8080/webhook`
   - **Secret token:** = `WEBHOOK_SECRET`
   - **Trigger:** ✅ Comments (Note events)
   - SSL verification: off
3. ⚠️ Admin Area → **Settings → Network → Outbound requests** → разрешить вебхуки
   в локальную сеть (иначе `Url is blocked`).
4. Проверка: в любой issue с дочерними Task напиши `/expand-tasks` — бот ответит
   планом комментарием.

## ⚠️ Перед боевым использованием

- **Необратимо** — откатов нет. Сначала `/expand-tasks` (план), потом `confirm`.
- **GraphQL схемозависим** — сверь `workItemConvert`, `workItemAddLinkedItems` на
  `https://git.fakspro.ru/-/graphql-explorer` для 17.6. Логика в `expand.py`.
- **Тест на песочнице** (отдельный проект, 1–2 тестовых таска) перед группой.

## TODO (после переезда)

- [ ] Перевести Matrix/отчёты-комменты на `botkit` (сейчас свои хелперы).
- [ ] Образ переименован: `gitlab-webhook-expand-tasks` → `gitlab-expand-tasks`.
