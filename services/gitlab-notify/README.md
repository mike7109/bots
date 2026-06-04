# gitlab-notify

Уведомления из GitLab в Matrix, но формат и маршрутизация — **в конфиге**, а не
зашиты в GitLab. Вёрстку правишь в `templates/`, правила/получателей — в
`config.yaml`, секреты — в `.env`. Без пересборки образа.

Пример сообщения в Element (assignee получает живой пинг):

> 🏷 чижик  **@Михаил Бахмутский**
> [#46 (closed) Интеграция вебхука](https://git.fakspro.ru/...) — закрыта

## Что умеет

| Источник | События | Шаблон | Куда (`to`) |
|---|---|---|---|
| Webhook (мгновенно) | issue открыта / закрыта / переоткрыта | `issue` | `room` |
| Cron (раз в день) | дедлайн задачи — **завтра** | `due_soon` | `room` |
| Cron (раз в день) | задача **просрочена** | `overdue` | `dm` (🔕 выкл.) |

- **Личка (DM) сейчас отключена** — правило `overdue` в `config.yaml` закомментировано.
  Транспорт `dm` и шаблон `overdue` на месте, включается одной строкой (см.
  «Назначения и личка»).
- GitLab 17.6 **не шлёт вебхуки по Task** (work items) — поэтому дедлайны/просрочка
  идут через `cron.py` (опрос API), а не через webhook.

```
GitLab webhook ─┐
                ├─▶ normalize → Event → Engine(rules) → Renderer(Jinja) ─▶ room / dm
cron.py (API) ──┘
```

---

## Откуда брать каждую настройку

Файлы: секреты → `.env` (из `.env.example`); комната/люди/правила → `config.yaml`.

### `.env` (секреты)

| Переменная | Что это | Где взять |
|---|---|---|
| `MATRIX_HOMESERVER` | адрес Matrix | `https://matrix.fakspro.ru` (проверка: `https://fakspro.ru/.well-known/matrix/client` → `base_url`) |
| `MATRIX_TOKEN` | токен бота `@gitlab-bot` | переиспользуй существующий. Получить: Element под ботом → **Аватар → Все настройки → Справка и сведения → Дополнительно → Токен доступа** (`syt_...`). Не выходить из сессии — иначе токен инвалидируется. CLI-вариант ниже. |
| `MATRIX_ROOM` | id общей комнаты | из `createRoom`/Element (ниже). **Без** `:fakspro.ru`. Переопределяет `defaults.room_id` в `config.yaml` — держим id в env, чтобы конфиг в git не расходился с сервером. |
| `WEBHOOK_SECRET` | секрет вебхука | сгенерь сам: `openssl rand -hex 24`. Это же значение вставишь в Secret token вебхука в GitLab. |
| `GITLAB_URL` | адрес GitLab | `https://git.fakspro.ru` |
| `GITLAB_TOKEN` | PAT для опроса API (cron) | GitLab под бот-аккаунтом → **User settings → Access Tokens** → scope `read_api` (хватит на чтение). |
| `GITLAB_GROUP_ID` | id группы для опроса дедлайнов | команда ниже (для FAKSPRO — `3`). |
| `LOG_LEVEL` | `INFO` / `DEBUG` | по вкусу |

Получить токен бота без UI:
```bash
curl -XPOST 'https://matrix.fakspro.ru/_matrix/client/v3/login' \
  -H 'Content-Type: application/json' \
  -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"gitlab-bot"},"password":"ПАРОЛЬ_БОТА","initial_device_display_name":"gitlab-notify"}'
# access_token из ответа -> MATRIX_TOKEN
```

Узнать id группы:
```bash
GL="личный_токен_gitlab"
curl -s --header "PRIVATE-TOKEN: $GL" \
  "https://git.fakspro.ru/api/v4/groups?search=FAKSPRO" | jq '.[] | {id, full_path}'
```

### `config.yaml` (поведение)

| Поле | Что это | Где взять |
|---|---|---|
| `defaults.to` | назначение по умолчанию | `[room]` (общая комната) / `[dm]` (личка) / оба. Правило может переопределить своим `to:`. |
| `defaults.room_id` | запасной id комнаты | обычно не трогаем — реальный id берётся из env `MATRIX_ROOM`. |
| `matrix_domain` | домен для пингов | `fakspro.ru` |
| `users.<login>` | связка логина GitLab → mxid/имя | mxid = `@<login>:fakspro.ru`. Логины — те же, что в GitLab; имена для красоты пилюли. |
| `rules` | типы уведомлений | каждое правило: `event`/`actions` (триггер), `template` (вид), `to` (куда: `room`/`dm`), `mention` (кому пинг). Ключ `event:` (не `on:` — YAML ломает). |

Если комнаты ещё нет — создать **под токеном бота** (бот сразу внутри и без E2EE):
```bash
TOKEN="syt_...токен_бота..."
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  "https://matrix.fakspro.ru/_matrix/client/v3/createRoom" \
  -d '{"name":"🔔 GitLab — infra","preset":"private_chat","visibility":"private"}'
# room_id из ответа -> env MATRIX_ROOM (в .env)
```

---

## Запуск

### Локально (отладка)
```bash
cd services/gitlab-notify
cp .env.example .env            # заполнить секреты
# из корня монорепо поставить общую либу + зависимости:
pip install -e ../../libs/botkit fastapi "uvicorn[standard]"
set -a; source .env; set +a
uvicorn app:app --reload --port 8081
curl -s localhost:8081/healthz  # {"status":"ok"}
```
Проверить рендер без Matrix: правишь `config.yaml`/`templates/` и шлёшь тестовый
payload на `/webhook` (с заголовком `X-Gitlab-Token: $WEBHOOK_SECRET`).

### Docker (одиночно)
```bash
cd services/gitlab-notify
cp .env.example .env            # заполнить
# образ собирается из КОРНЯ монорепо (нужна libs/):
docker build -f Dockerfile -t gitlab-notify ../..
IMAGE=gitlab-notify docker compose up -d
curl -s localhost:8081/healthz
```
`config.yaml` и `templates/` смонтированы томом — поправил, `docker compose restart`.

### Прод
Через общий стек — см. **`../../deploy/README.md`** (образы из GHCR, все боты разом).

---

## Подключить к GitLab

1. Проект или группа FAKSPRO → **Settings → Webhooks → Add new webhook**:
   - **URL:** `http://127.0.0.1:8081/webhook`
   - **Secret token:** = `WEBHOOK_SECRET`
   - **Trigger:** ✅ Issues events
   - SSL verification: off (http на localhost)
2. **Выключить старую встроенную** Matrix-интеграцию (Settings → Integrations →
   Matrix → Active off), иначе будет дубль.
3. ⚠️ Admin Area → **Settings → Network → Outbound requests** → разрешить вебхуки
   в локальную сеть (иначе хук молча падает `Url is blocked`).
4. **Test** на странице вебхука / создать тестовую issue → проверить комнату.

## Дедлайны (cron)

`cron.py` шлёт напоминания о задачах с дедлайном завтра. Повесить на ежедневный
запуск (host cron / systemd timer), пример в `../../deploy/README.md`:
```bash
docker compose run --rm gitlab-notify python cron.py
```

## Назначения и личка (DM)

Куда уходит уведомление задаёт поле `to:` у правила:

| `to` | Куда | Транспорт |
|---|---|---|
| `room` | общая комната (id из `MATRIX_ROOM`) | `MatrixTransport` |
| `dm` | личка assignee 1-на-1 | `MatrixDirectTransport` |
| `email` | (потом) | `EmailTransport` |

Можно несколько: `to: [room, dm]` — и в комнату, и в личку.

**Личка сейчас выключена.** Чтобы включить напоминания о просрочке в DM —
раскомментируй правило `overdue` в `config.yaml` и перезапусти бота. Механика:
на первое сообщение бот пришлёт человеку **инвайт в личку, его надо принять один
раз**, дальше молча. (Если на Synapse включён авто-join DM — принимать не нужно.)

## Менять под себя

- **Текст сообщения** → `templates/<template>.matrix.html.j2`
- **Триггер / кому пинг / куда (`to`)** → `config.yaml` (`rules`)
- **Новое назначение (почта и т.п.)** → транспорт в `libs/botkit/.../notify/transports/`
  (со своим `name`/`medium`), добавить в `to:`. Код бота не трогается.
