# Развёртывание issue-graph на сервере

Один контейнер: бэкенд (FastAPI) отдаёт собранный фронт статикой с того же
origin — нет CORS, httpOnly-cookie сессии работают «из коробки». Данные (конфиг
инстанса + слой связей) — в томе `/data`. Образ собирается в GitHub Actions и
публикуется в **GHCR** (`ghcr.io/<owner>/issue-graph`).

## 0. Что хранится и где
- `/data/.gl_config.json` — host GitLab, OAuth app (id/secret), бот, тариф, рубильник.
- `/data/issue_graph.db` — SQLite-слой типов/направлений связей (наш слой поверх GitLab Free).
- Сами задачи НЕ хранятся — приложение работает на API GitLab.

## 1. Собрать образ (GitHub Actions → GHCR)
Образ собирается автоматически при пуше в `main` или по тегу `v*`
(workflow `.github/workflows/issue-graph.yml`).

После первого успешного билда:
1. На GitHub: **Packages → issue-graph** появится пакет. Сделай его видимость
   нужной (private/public) в настройках пакета.
2. Если пакет приватный — на сервере залогинься в GHCR:
   ```bash
   echo <GITHUB_TOKEN_with_read:packages> | docker login ghcr.io -u <github-user> --password-stdin
   ```

(Локально образ тоже можно собрать: `docker build -t issue-graph services/issue-graph`.)

## 2. Запуск на сервере
```bash
# на сервере, в любой папке:
mkdir issue-graph && cd issue-graph
# положи сюда docker-compose.yml и .env.example из services/issue-graph/
cp .env.example .env
nano .env        # заполни GHCR_OWNER, ADMIN_PASSWORD, PUBLIC_URL, BIND
docker compose pull
docker compose up -d
docker compose logs -f      # убедись, что поднялось
```

`.env` (минимум):
```
GHCR_OWNER=<твой-github-логин-или-org>
ADMIN_PASSWORD=<длинный случайный пароль>
PUBLIC_URL=http://<адрес-как-в-браузере>      # см. ниже
BIND=0.0.0.0:8000                             # или 127.0.0.1:8000 за прокси
```

## 3. Первичная настройка (один раз, в админке)
1. Открой `PUBLIC_URL/#admin`, войди паролем `ADMIN_PASSWORD`.
2. **GitLab → Host** — адрес твоего GitLab.
3. **OAuth-приложение**: в GitLab (Admin Area → Applications или Group →
   Applications) создай приложение:
   - scope: `api`, тип **Confidential**;
   - **Redirect URI** — ровно тот, что показан в админке
     (`PUBLIC_URL/api/oauth/callback`).
   Скопируй Application ID и Secret в админку, сохрани.
4. (Опц.) **Бот уведомлений** — URL и `poke_token` для кнопки «🔔 Пнуть».
5. (Опц.) **Тариф PRO** — включай, только если в GitLab реально Premium/Ultimate
   (инструкция проверки — там же в админке).
6. **Рубильник** — глобально включить/выключить приложение для всех.

После этого пользователи заходят на `PUBLIC_URL`, жмут «Войти через GitLab» и
видят свои задачи.

## 4. Развёртывание в локальной сети по HTTP (без сертификата)
Сертификат **не нужен** — cookie сессий не помечены `Secure`, всё работает по
HTTP. Настройки:
- `PUBLIC_URL=http://<IP-или-имя-сервера-в-сети>:8000`
  (например `http://192.168.1.50:8000` — именно так, как другие машины
  открывают приложение в браузере; не `localhost`).
- `BIND=0.0.0.0:8000` — чтобы сервер был доступен другим машинам в сети.
- В GitLab OAuth-приложении Redirect URI = `http://192.168.1.50:8000/api/oauth/callback`.
  GitLab разрешает http-redirect для OAuth-приложений.

> Важно: `PUBLIC_URL` и Redirect URI в GitLab должны совпадать с тем адресом,
> по которому пользователь реально открывает сайт (тот же хост/порт), иначе
> OAuth-редирект и cookie не сойдутся.

## 5. За reverse-proxy (если есть домен/HTTPS)
Любой прокси (nginx/Caddy/Traefik) проксирует на контейнер `:8000`.
- `BIND=127.0.0.1:8000`, `PUBLIC_URL=https://issuegraph.example.com`.
- Caddy одной строкой даёт HTTPS:
  ```
  issuegraph.example.com {
      reverse_proxy 127.0.0.1:8000
  }
  ```

## 6. Обновление
```bash
docker compose pull && docker compose up -d
```
Том `ig-data` сохраняет настройки и связи между обновлениями.

## 7. Бэкап
Достаточно сохранить том `ig-data` (или файлы `/data/.gl_config.json` и
`/data/issue_graph.db`).
```bash
docker run --rm -v issue-graph_ig-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/ig-data-backup.tgz -C /data .
```
