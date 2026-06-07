"""Конфиг бэкенда issue-graph.

Подключение к GitLab можно задать:
  1) env GITLAB_URL / GITLAB_TOKEN (приоритет при старте);
  2) в UI токеном (PAT/Group token);
  3) в UI через OAuth «Войти через GitLab».
Доменных данных приложение не хранит — только параметры подключения (в файле).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get(
        "ISSUE_GRAPH_CONFIG", Path(__file__).parent.parent / ".gl_config.json"
    )
)
# базовые URL для OAuth-редиректов (можно переопределить через env)
REDIRECT_BASE = os.environ.get("OAUTH_REDIRECT_BASE", "http://localhost:8000").rstrip("/")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")


@dataclass(frozen=True)
class Settings:
    concurrency: int
    cache_ttl: int
    cors_origins: list[str]

    @staticmethod
    def load() -> "Settings":
        origins = os.environ.get(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
        return Settings(
            concurrency=int(os.environ.get("GITLAB_CONCURRENCY", "12")),
            cache_ttl=int(os.environ.get("CACHE_TTL", "60")),
            cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
        )


@dataclass
class Connection:
    url: str
    token: str
    token_type: str = "pat"  # pat | oauth


# ── файловое хранилище конфига (dict) ───────────────────────────────────────
def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg))


def initial_connection() -> Connection | None:
    """Старт: env-токен → сохранённое подключение → ничего."""
    env_token = os.environ.get("GITLAB_TOKEN")
    if env_token:
        url = os.environ.get("GITLAB_URL", "http://localhost:8929").rstrip("/")
        return Connection(url=url, token=env_token, token_type="pat")
    cfg = load_config()
    if cfg.get("token") and cfg.get("url"):
        return Connection(
            url=cfg["url"].rstrip("/"),
            token=cfg["token"],
            token_type=cfg.get("token_type", "pat"),
        )
    return None


def save_connection(conn: Connection, refresh_token: str | None = None) -> None:
    cfg = load_config()
    cfg.update(url=conn.url, token=conn.token, token_type=conn.token_type)
    if refresh_token is not None:
        cfg["refresh_token"] = refresh_token
    save_config(cfg)


# ── инстанс-конфиг (управляется админом) ────────────────────────────────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def get_instance_url() -> str | None:
    """Адрес GitLab для всего инстанса: из файла (админ) либо из env."""
    return load_config().get("gitlab_url") or os.environ.get("GITLAB_URL")


def is_enabled() -> bool:
    """Глобальный рубильник: приложение доступно пользователям?"""
    return not load_config().get("disabled", False)


def set_enabled(enabled: bool) -> None:
    cfg = load_config()
    cfg["disabled"] = not enabled
    save_config(cfg)


def get_oauth_app() -> dict | None:
    """OAuth-приложение инстанса (client_id/secret). URL берётся из instance_url."""
    return load_config().get("oauth")


def get_bot() -> dict | None:
    """Бот уведомлений инстанса: {url, secret}."""
    return load_config().get("notify_bot")


def get_pro() -> bool:
    """Режим PRO (Premium/Ultimate) — задаётся админом, по умолчанию выкл."""
    return bool(load_config().get("pro", False))


def set_pro(pro: bool) -> None:
    cfg = load_config()
    cfg["pro"] = bool(pro)
    save_config(cfg)


def save_instance(gitlab_url: str | None, client_id: str | None,
                  client_secret: str | None, bot_url: str | None,
                  bot_secret: str | None) -> None:
    """Сохранить инстанс-настройки. Пустые секреты не затирают существующие."""
    cfg = load_config()
    if gitlab_url is not None:
        cfg["gitlab_url"] = gitlab_url.rstrip("/")
    if client_id is not None:
        oauth = cfg.get("oauth") or {}
        oauth["client_id"] = client_id
        if client_secret:  # пусто = оставить прежний
            oauth["client_secret"] = client_secret
        cfg["oauth"] = oauth
    if bot_url is not None:
        if bot_url:
            bot = cfg.get("notify_bot") or {}
            bot["url"] = bot_url.rstrip("/")
            if bot_secret is not None and bot_secret != "":
                bot["secret"] = bot_secret
            cfg["notify_bot"] = bot
        else:
            cfg.pop("notify_bot", None)
    save_config(cfg)


def redirect_uri() -> str:
    # через фронтовый origin (vite-прокси), чтобы httpOnly-cookie сессии
    # корректно ставились на том же origin, что и остальные /api-запросы.
    base = os.environ.get("OAUTH_REDIRECT_BASE") or FRONTEND_URL
    return f"{base.rstrip('/')}/api/oauth/callback"
