# ТЗ: эндпоинт «пнуть исполнителя» в gitlab-notify

Issue-graph умеет отправлять исполнителю задачи напоминание через бота
`gitlab-notify`. Клиентская часть готова: при нажатии «🔔 Пнуть исполнителя»
issue-graph делает HTTP-запрос на бот. Нужно реализовать в `gitlab-notify`
приёмный эндпоинт.

## Контракт (что присылает issue-graph)

```
POST {BOT_URL}/api/poke
Content-Type: application/json
```

Тело:

```json
{
  "gitlab_username": "alice",
  "issue": {
    "iid": 5,
    "title": "Кнопка оплаты не активна в Safari",
    "web_url": "http://gitlab.local/graphlab/webapp/-/issues/5",
    "project_path": "graphlab/webapp"
  },
  "message": null
}
```

- `gitlab_username` — GitLab-логин исполнителя (по нему резолвим адресата).
- `issue` — данные задачи для текста уведомления.
- `message` — необязательный готовый текст. Если `null`/пусто — бот сам
  формирует сообщение из `issue`.

## Что должен делать бот

1. Резолвить `gitlab_username` → адресата в Matrix через уже существующий
   `Identity.matrix_id(login)` (см. `wiring.py`: `identity = Identity(...)`).
2. Отправить **личное сообщение (DM)** этому пользователю через уже
   подключённый транспорт `MatrixDirectTransport` (в `wiring.py` он лежит в
   `transports["dm"]`).
3. Текст по умолчанию (если `message` пуст), например:
   > 🔔 Тебя пнули по задаче **#{iid} {title}**
   > {web_url}
4. Вернуть результат (см. ниже).

## Ответ

- Успех: `200 {"ok": true, "delivered": true}`.
- Пользователь не сопоставлен с Matrix: `422 {"ok": false, "reason": "user_not_mapped"}`.
- Прочие ошибки: соответствующий 4xx/5xx с телом `{"ok": false, "reason": "..."}`.

Issue-graph показывает текст ошибки пользователю, поэтому `reason` желательно
человекочитаемый.

## Безопасность (на усмотрение)

Эндпоинт внутренний (issue-graph → бот в одной сети). При желании — общий
секрет: issue-graph будет слать `Authorization: Bearer <token>`, бот проверяет.
Если решите так — сообщите, добавлю отправку заголовка в issue-graph
(в настройках появится поле «токен бота»).

## Где подключить в gitlab-notify

- Роут можно добавить в `admin.py`/`app.py` (FastAPI-приложение уже есть).
- Контекст с `identity` и транспортами собирается в `wiring.py:build_context()`
  (в `app.py` он уже создан как `ctx`).
- Реальная отправка DM — через `ctx.transports["dm"]` (или как назван
  MatrixDirectTransport в контексте), метод отправки — посмотреть по аналогии
  с тем, как шлются дайджесты/алерты (`alerts.py`).
