"""Тест параллельной пагинации (этап 4 ROADMAP) — без сети.

`GitLabClient.paginate` должен:
  * при наличии `X-Total-Pages` тянуть хвост страниц ПАРАЛЛЕЛЬНО (один gather),
    а не «страница за страницей»;
  * без `X-Total-Pages` (keyset/старые версии) — фолбэк на последовательный
    обход по `X-Next-Page`.

Сеть не трогаем: подменяем низкоуровневый `_get` фейковым ответом, считаем,
сколько запросов шло одновременно (фиксируем максимум параллелизма).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gitlab_client import GitLabClient  # noqa: E402


class _Resp:
    def __init__(self, payload, headers):
        self._payload = payload
        self.headers = headers
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _make_client() -> GitLabClient:
    # реальный клиент создаёт httpx.AsyncClient, но paginate его не трогает —
    # ходит только через self._get, который мы подменим.
    return GitLabClient("https://gl.example", "tok", concurrency=12)


def test_parallel_pagination_uses_total_pages_header():
    """X-Total-Pages → хвост страниц тянется одним параллельным заходом."""
    cl = _make_client()
    total_pages = 5
    per_page = 2
    pages_seen: list[int] = []
    inflight = 0
    max_inflight = 0

    async def fake_get(path, params=None):
        nonlocal inflight, max_inflight
        page = int((params or {}).get("page", 1))
        pages_seen.append(page)
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        try:
            await asyncio.sleep(0.01)  # удержим запрос «в полёте» для замера
        finally:
            inflight -= 1
        # каждая страница — per_page элементов
        items = [{"id": (page - 1) * per_page + i} for i in range(per_page)]
        return _Resp(items, {"x-total-pages": str(total_pages)})

    cl._get = fake_get  # type: ignore[assignment]
    out = asyncio.run(cl.paginate("/groups/1/issues", {"per_page": per_page}))

    # все страницы собраны
    assert len(out) == total_pages * per_page
    assert {p for p in pages_seen} == set(range(1, total_pages + 1))
    # страница 1 синхронно, 2..5 — параллельно: пик параллелизма > 1
    assert max_inflight >= 2, f"ожидали параллель, max_inflight={max_inflight}"


def test_sequential_fallback_without_total_pages():
    """Без X-Total-Pages идём последовательно по X-Next-Page (keyset-режим)."""
    cl = _make_client()
    inflight = 0
    max_inflight = 0
    pages_seen: list[int] = []
    last_page = 3

    async def fake_get(path, params=None):
        nonlocal inflight, max_inflight
        page = int((params or {}).get("page", 1))
        pages_seen.append(page)
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        try:
            await asyncio.sleep(0.005)
        finally:
            inflight -= 1
        headers = {}
        if page < last_page:
            headers["x-next-page"] = str(page + 1)
        return _Resp([{"id": page}], headers)

    cl._get = fake_get  # type: ignore[assignment]
    out = asyncio.run(cl.paginate("/groups/1/issues"))

    assert [o["id"] for o in out] == [1, 2, 3]
    assert pages_seen == [1, 2, 3]
    # последовательный обход — параллелизма быть не должно
    assert max_inflight == 1


def test_single_page_no_extra_requests():
    """Одна страница (X-Total-Pages=1) → только один запрос, без хвоста."""
    cl = _make_client()
    calls = 0

    async def fake_get(path, params=None):
        nonlocal calls
        calls += 1
        return _Resp([{"id": 1}, {"id": 2}], {"x-total-pages": "1"})

    cl._get = fake_get  # type: ignore[assignment]
    out = asyncio.run(cl.paginate("/labels"))
    assert len(out) == 2
    assert calls == 1


def test_empty_first_page():
    """Пустой первый ответ → пустой результат, без падений."""
    cl = _make_client()

    async def fake_get(path, params=None):
        return _Resp([], {"x-total-pages": "0"})

    cl._get = fake_get  # type: ignore[assignment]
    out = asyncio.run(cl.paginate("/labels"))
    assert out == []
