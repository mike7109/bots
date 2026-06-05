# Миграция gitlab-notify: старый бот → новый движок (botkit + админка + планировщик)

Старая версия **несовместима** по конфигу и инфре, поэтому это **замена**, не
апдейт на месте «pull и всё». Делается на сервере с ботами (где `/opt/bots`,
сервис крутится из `services/gitlab-notify/docker-compose.yml`).

Что меняется против старого:
- движок читает `defaults: to:[room] + room_id` (room теперь из env `MATRIX_ROOM`);
- нужен **том `/data`** под состояние (дедуп/настройки/логи) — иначе падёт на старте;
- появились веб-админка (`/admin`) и внутренний планировщик рассылок.

## 1. Обновить код (старый бот не нужен — чистый reset)
```bash
cd /opt/bots
git fetch origin
git reset --hard origin/main        # сбрасывает локальные правки config.yaml/compose
```
`.env` не тронется (он не в git). Реальные users уже лежат в новом `config.yaml`,
room уедет в env (шаг 2).

## 2. Дописать .env (секреты уже есть от старого бота — не трогаем)
```bash
cd services/gitlab-notify
$EDITOR .env
```
Добавить строки:
```
MATRIX_ROOM=!hnt04WMGva5OGP9nw5vau3zDl5AvMZVg-BVLOSCSQug   # боевая «🔔 GitLab — infra»
BIND_ADDR=172.16.1.10        # чтобы /admin был доступен по LAN
SCHEDULER_ENABLED=false      # на старте тихо: вебхуки да, авторассылок нет
TZ=Europe/Moscow
STATE_DB=/data/state.db
ADMIN_PASSWORD=              # пока пусто (админка выключена); добавишь на шаге 5
```
Существующие `MATRIX_HOMESERVER / MATRIX_TOKEN / WEBHOOK_SECRET / GITLAB_*` —
оставить как есть.

## 3. Выкатить
```bash
docker compose pull
docker compose up -d
docker compose ps
curl -s http://127.0.0.1:8081/healthz            # {"status":"ok"}
docker compose logs --tail=40 gitlab-notify       # без traceback; строки "scheduler" нет (off)
```

## 4. Проверить вебхуки (как у старого бота)
Открой/закрой любой issue → сообщение в «🔔 GitLab — infra». Это новый движок,
вид тот же. Если ок — база работает.

## 5. Включить админку (когда готов завести пароль)
```bash
# .env:  ADMIN_PASSWORD=<длинный секрет>
docker compose up -d
# с ноута:
ssh -L 8081:172.16.1.10:8081 misha_bah@172.16.1.10
# браузер: http://localhost:8081/admin
```
Прокликай вкладки; во «Рассылки» жми «▶ Запустить сейчас» (например, тим-сводка) —
сообщение придёт в боевую комнату, результат — на карточке.

## 6. Включить автопилот (планировщик)
```bash
# .env:  SCHEDULER_ENABLED=true
docker compose up -d
```
Дни/время каждой рассылки — в админке, вкладка «Рассылки».

## 7. (опц.) Личные DM
В админке «Правила» → включи тумблером «Просрочка» / «Личный дайджест».
Бот разошлёт DM-инвайты исполнителям (висят «ждёт», пока не примут; в комнату
ничего не утекает). Кнопка «Позвать принять бота» — пнуть непринявших.

## Откат
Старый образ остался в GHCR по sha:
```bash
# в .env:  IMAGE=ghcr.io/mike7109/gitlab-notify:sha-<старый-короткий>
docker compose up -d
```
(старый sha — GHCR → Packages → gitlab-notify). По нужде старый бот не нужен —
вряд ли понадобится.
