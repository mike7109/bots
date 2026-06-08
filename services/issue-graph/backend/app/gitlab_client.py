"""Тонкий async-клиент к GitLab REST API.

REST выбран намеренно: endpoint'ы стабильны между версиями и одинаково
работают на CE/Free и на EE/Premium — отличается лишь набор данных, который
инстанс реально отдаёт. Фичи (epics, blocking-связи, iterations) определяются
динамически через probe, чтобы одно и то же приложение работало на любой
подписке (graceful degradation).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class Capabilities:
    version: str = ""
    enterprise: bool = False
    tier: str = "free"  # free | premium | ultimate
    epics: bool = False
    blocking_links: bool = False
    iterations: bool = False
    graphql: bool = False  # доступен ли GraphQL-эндпоинт (быстрый путь сборки)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "enterprise": self.enterprise,
            "tier": self.tier,
            "epics": self.epics,
            "blocking_links": self.blocking_links,
            "iterations": self.iterations,
            "milestones": True,  # всегда доступны
            "graphql": self.graphql,
        }


def _auth_headers(token: str, token_type: str) -> dict[str, str]:
    """OAuth-токены идут через Bearer, личные (PAT) — через PRIVATE-TOKEN."""
    if token_type == "oauth":
        return {"Authorization": f"Bearer {token}"}
    return {"PRIVATE-TOKEN": token}


class GitLabClient:
    def __init__(self, base_url: str, token: str, concurrency: int = 10,
                 token_type: str = "pat"):
        self.base = base_url.rstrip("/") + "/api/v4"
        self.token = token
        self.token_type = token_type
        self._refresh_cb = None  # async () -> str | None  (новый access_token)
        self._client = httpx.AsyncClient(
            headers=_auth_headers(token, token_type),
            timeout=httpx.Timeout(30.0),
        )
        self._sem = asyncio.Semaphore(concurrency)

    def set_refresh_cb(self, cb) -> None:
        self._refresh_cb = cb

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        """Единая точка запросов: при 401 на OAuth пробуем refresh и повтор."""
        async with self._sem:
            r = await self._client.request(method, self.base + path, **kw)
        if r.status_code == 401 and self._refresh_cb is not None:
            new = await self._refresh_cb()
            if new:
                self.token = new
                self._client.headers.update(_auth_headers(new, self.token_type))
                async with self._sem:
                    r = await self._client.request(method, self.base + path, **kw)
        return r

    async def post(self, path: str, **kw) -> httpx.Response:
        return await self.request("POST", path, **kw)

    async def put(self, path: str, **kw) -> httpx.Response:
        return await self.request("PUT", path, **kw)

    async def delete(self, path: str, **kw) -> httpx.Response:
        return await self.request("DELETE", path, **kw)

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        return await self.request("GET", path, params=params or {})

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        r = await self._get(path, params)
        r.raise_for_status()
        return r.json()

    async def count(self, path: str, params: dict | None = None) -> int | None:
        """Дешёвый подсчёт через заголовок X-Total (per_page=1)."""
        p = dict(params or {})
        p["per_page"] = 1
        r = await self._get(path, p)
        if r.status_code >= 300:
            return None
        total = r.headers.get("x-total")
        return int(total) if total is not None else None

    async def paginate(self, path: str, params: dict | None = None) -> list[dict]:
        """Собирает все страницы списочного endpoint'а.

        Этап 4: первую страницу тянем всегда; если GitLab отдал `X-Total-Pages`,
        остальные страницы запрашиваем ПАРАЛЛЕЛЬНО (под общим семафором клиента)
        вместо «страница за страницей». Без заголовка (keyset-режим, старые версии)
        — фолбэк на последовательный обход по `X-Next-Page`.
        """
        params = dict(params or {})
        params.setdefault("per_page", 100)

        first = dict(params, page=1)
        r = await self._get(path, first)
        r.raise_for_status()
        out: list[dict] = list(r.json() or [])
        if not out:
            return out

        total_pages = r.headers.get("x-total-pages")
        if total_pages is not None:
            # известно общее число страниц → тянем хвост одним gather.
            try:
                last = int(total_pages)
            except ValueError:
                last = 1
            if last <= 1:
                return out

            async def fetch(page: int) -> list[dict]:
                rp = await self._get(path, dict(params, page=page))
                rp.raise_for_status()
                return rp.json() or []

            rest = await asyncio.gather(*(fetch(p) for p in range(2, last + 1)))
            for batch in rest:
                out.extend(batch)
            return out

        # фолбэк: keyset/offset без X-Total-Pages — последовательно по X-Next-Page.
        next_page = r.headers.get("x-next-page")
        while next_page:
            r = await self._get(path, dict(params, page=int(next_page)))
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            out.extend(batch)
            next_page = r.headers.get("x-next-page")
        return out

    # ── capability detection ────────────────────────────────────────────────
    async def detect_capabilities(self, pro: bool = False) -> Capabilities:
        """Версия/edition — из /version (доступно любому). Тариф (epics/blocking/
        iterations) НЕ определяем через админский /license: режим PRO задаёт
        администратор в настройках инстанса."""
        caps = Capabilities()
        ver = await self.get_json("/version")
        caps.version = ver.get("version", "")
        caps.enterprise = bool(ver.get("enterprise"))
        caps.tier = "premium" if pro else "free"
        caps.epics = pro
        caps.blocking_links = pro
        caps.iterations = pro
        # GraphQL: пробный запрос; недоступен на старых CE / при выключенном API.
        # Локальный импорт — модуль gitlab_graphql тянет наш же клиент.
        from .gitlab_graphql import probe_graphql
        caps.graphql = await probe_graphql(self)
        return caps


@dataclass
class Scope:
    """Где строим граф: группа (со всеми подпроектами) или один проект."""

    kind: str  # "group" | "project"
    id: str

    @property
    def issues_path(self) -> str:
        if self.kind == "group":
            return f"/groups/{self.id}/issues"
        return f"/projects/{self.id}/issues"
