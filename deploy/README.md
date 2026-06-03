# deploy — весь стек ботов на сервере с GitLab

Боты крутятся **на том же сервере, где GitLab**, образы тянутся готовыми из
GHCR, порты слушают только `127.0.0.1`, вебхуки GitLab указывают на localhost
того же сервера. Никакого Kubernetes.

```
push в GitHub (main / тег v*)
   │  GitHub Actions → ghcr.io/mike7109/<service>
   ▼
[сервер git.fakspro.ru]  docker compose pull && up -d
   ▲
   │  webhook → http://127.0.0.1:<порт>/webhook
GitLab (issue / коммент)
```

## Карта портов

| Сервис | Порт (127.0.0.1) | Webhook URL | Триггер |
|---|---|---|---|
| `gitlab-notify` | `8081` | `http://127.0.0.1:8081/webhook` | Issues events |
| `gitlab-expand-tasks` | `8080` | `http://127.0.0.1:8080/webhook` | Comments (Note) |

## 0. Предусловия (один раз)

1. **Docker + compose** на сервере GitLab.
2. **Образы публичные в GHCR.** После первого билда CI пакеты приватные:
   GitHub → профиль → **Packages** → пакет → **Package settings → Change
   visibility → Public** (для каждого сервиса). Иначе `docker pull` потребует
   логина.
3. ⚠️ **Разрешить вебхуки в локальную сеть:** GitLab Admin Area → **Settings →
   Network → Outbound requests** → «Allow requests to the local network from
   webhooks and integrations» (или добавить `127.0.0.1` в allowlist). Без этого
   хуки молча падают с `Url is blocked`.
4. Бот-аккаунты и токены — см. README конкретного сервиса
   (`../services/<bot>/README.md`, раздел «Откуда брать настройки»).

## 1. Положить файлы на сервер

Нужны `deploy/docker-compose.yml` и `.env`-файлы. Проще склонировать репо:
```bash
sudo git clone https://github.com/mike7109/bots.git /opt/bots
cd /opt/bots/deploy
```
> Compose монтирует `../services/gitlab-notify/{config.yaml,templates}` в
> контейнер — поэтому нужен весь репо, а не только папка deploy.

## 2. Заполнить секреты

По одному `.env` на сервис (значения — из README сервисов):
```bash
cp gitlab-notify.env.example       gitlab-notify.env
cp gitlab-expand-tasks.env.example gitlab-expand-tasks.env
$EDITOR gitlab-notify.env gitlab-expand-tasks.env
```
А также проставить реальный **room id** и `users` в
`../services/gitlab-notify/config.yaml`.

## 3. Поднять

```bash
docker compose pull
docker compose up -d
docker compose ps
curl -s http://127.0.0.1:8081/healthz   # gitlab-notify
curl -s http://127.0.0.1:8080/healthz   # gitlab-expand-tasks
```

## 4. Подключить вебхуки

В GitLab для каждого сервиса — см. таблицу портов выше и раздел «Подключить к
GitLab» в README сервиса. Не забудь **выключить старую встроенную Matrix-интеграцию**.

## 5. Дедлайны (cron) — только для gitlab-notify

`cron.py` шлёт напоминания о задачах с дедлайном завтра. Повесить на хостовый cron:
```cron
# /etc/cron.d/gitlab-notify  — каждый день в 09:00
0 9 * * *  root  cd /opt/bots/deploy && docker compose run --rm gitlab-notify python cron.py >> /var/log/gitlab-notify-cron.log 2>&1
```

## Обновление

```bash
cd /opt/bots && git pull
cd deploy && docker compose pull && docker compose up -d
```
CI собирает только изменённый сервис, так что `pull` обновит лишь его образ.
Поправил только текст/правила в `config.yaml`/`templates/` (они смонтированы
томом) — образ не нужен, хватит `docker compose restart gitlab-notify`.

## Логи / диагностика

```bash
docker compose logs -f gitlab-notify
docker compose logs -f gitlab-expand-tasks
```

| Симптом | Причина / решение |
|---|---|
| webhook падает `Url is blocked` | не включён Outbound requests в локальную сеть (п.0.3) |
| `docker pull` просит логин | пакет в GHCR ещё приватный (п.0.2) |
| Matrix `M_FORBIDDEN: not in room` | бот не в комнате — создай комнату под ботом |
| сообщения не приходят, комната зашифрована | E2EE не поддерживается — пересоздать комнату без шифрования |
| `not in room` при верном токене | к room id дописан `:fakspro.ru` — у нас id **без** домена |

## Если GitLab сам в Docker

`127.0.0.1` из контейнера GitLab — это не хост. Тогда: либо подними ботов в той
же compose-сети, что GitLab, и используй `http://gitlab-notify:8080/webhook`,
либо повесь сервисы на адрес docker-бриджа хоста. Если GitLab — Omnibus на
хосте, `127.0.0.1` работает как есть.
