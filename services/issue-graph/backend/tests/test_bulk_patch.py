"""Тесты массового действия лассо (Этап 7 ROADMAP) — без сети.

Этап 7 «левая панель + лупа + bulk»: `POST /api/issues/bulk` применяет один набор
изменений ко многим задачам под семафором клиента и НЕ валит всю операцию из-за
ошибки одной задачи (частичные ошибки). Покрываем без сети:
  * payload к GitLab формируется из общего `_patch_payload` (как у одиночного PATCH);
  * успех по всем → ok=True, succeeded=N, failed=0;
  * ошибка одной задачи (HTTP 4xx / сетевая) не роняет пачку → ok=False,
    succeeded/failed считаются верно, остальные задачи обновлены;
  * пустой набор изменений / пустой список задач → 400.

Сеть не нужна: подменяем client() фейком, который перехватывает PUT и может
вернуть ошибку для конкретной задачи (как в test_issue_detail/test_swr_cache).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

# изолируем store-БД до импорта пакета (как в остальных тестах)
_TMP_DB = Path("/tmp/issue-graph-bulk-test.db")
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["ISSUE_GRAPH_DB"] = str(_TMP_DB)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main  # noqa: E402


class _BulkClient:
    """Фейковый клиент: запоминает все PUT; для iid из `fail_iids` возвращает 409,
    для iid из `raise_iids` бросает сетевую ошибку (как реальный таймаут)."""

    def __init__(self, fail_iids: set[int] | None = None,
                 raise_iids: set[int] | None = None):
        self.puts: list[tuple[str, dict]] = []
        self.fail_iids = fail_iids or set()
        self.raise_iids = raise_iids or set()

    async def put(self, path, json=None):  # noqa: A002 (имя как в httpx)
        self.puts.append((path, json))
        # iid — последний сегмент пути /projects/{pid}/issues/{iid}
        iid = int(path.rsplit("/", 1)[-1])
        if iid in self.raise_iids:
            raise httpx.ConnectError("boom")

        class _R:
            def __init__(self, iid_: int, fail: bool):
                self.status_code = 409 if fail else 200
                self.text = "conflict" if fail else ""
                self._iid = iid_

            def json(self_inner):
                return {
                    "project_id": 10, "iid": self_inner._iid, "title": "T",
                    "state": "opened", "labels": [], "milestone": None,
                    "assignees": [],
                }
        return _R(iid, iid in self.fail_iids)


def _bulk(cl: _BulkClient, body: main.BulkPatch):
    """Прогнать bulk_patch_issues с подменённым client()."""
    orig = main.client
    main.client = lambda: cl  # type: ignore[assignment]
    try:
        return asyncio.run(main.bulk_patch_issues(body))
    finally:
        main.client = orig  # type: ignore[assignment]


def test_bulk_all_succeed_and_payload():
    cl = _BulkClient()
    body = main.BulkPatch(
        targets=[(10, 1), (10, 2), (10, 3)],
        patch=main.IssuePatch(state_event="close"),
    )
    res = _bulk(cl, body)
    assert res["ok"] is True
    assert res["succeeded"] == 3 and res["failed"] == 0
    # один PUT на задачу, payload — общий (state_event=close)
    assert len(cl.puts) == 3
    for _, json in cl.puts:
        assert json == {"state_event": "close"}


def test_bulk_partial_failure_does_not_abort():
    # задача iid=2 отдаёт 409 — остальные должны примениться
    cl = _BulkClient(fail_iids={2})
    body = main.BulkPatch(
        targets=[(10, 1), (10, 2), (10, 3)],
        patch=main.IssuePatch(milestone_id=7),
    )
    res = _bulk(cl, body)
    assert res["ok"] is False
    assert res["succeeded"] == 2 and res["failed"] == 1
    by_iid = {r["iid"]: r for r in res["results"]}
    assert by_iid[1]["ok"] and by_iid[3]["ok"]
    assert not by_iid[2]["ok"] and "error" in by_iid[2]
    # все три PUT всё равно ушли (ошибка одной не отменяет остальные)
    assert len(cl.puts) == 3


def test_bulk_network_error_isolated():
    # сетевая ошибка одной задачи ловится и не роняет gather
    cl = _BulkClient(raise_iids={1})
    body = main.BulkPatch(
        targets=[(10, 1), (10, 2)],
        patch=main.IssuePatch(assignee_ids=[42]),
    )
    res = _bulk(cl, body)
    assert res["succeeded"] == 1 and res["failed"] == 1
    by_iid = {r["iid"]: r for r in res["results"]}
    assert not by_iid[1]["ok"] and by_iid[2]["ok"]


def test_bulk_clear_assignees_payload():
    # пустой список снимает исполнителей → GitLab ждёт [0]
    cl = _BulkClient()
    body = main.BulkPatch(
        targets=[(10, 1)], patch=main.IssuePatch(assignee_ids=[]))
    res = _bulk(cl, body)
    assert res["ok"] is True
    assert cl.puts[0][1] == {"assignee_ids": [0]}


def test_bulk_empty_patch_raises():
    cl = _BulkClient()
    with pytest.raises(main.HTTPException):
        _bulk(cl, main.BulkPatch(targets=[(10, 1)], patch=main.IssuePatch()))


def test_bulk_empty_targets_raises():
    cl = _BulkClient()
    with pytest.raises(main.HTTPException):
        _bulk(cl, main.BulkPatch(
            targets=[], patch=main.IssuePatch(state_event="close")))
