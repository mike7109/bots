"""Тесты переноса задачи между проектами (Этап 8 ROADMAP, Kanban) — без сети.

Этап 8 «Kanban»: drag в колоночном виде «по проектам» переносит задачу в другой
проект через `POST /projects/{pid}/issues/{iid}/move` (у GitLab это отдельная
операция — меняется iid/project_id, а не обычный PATCH). Покрываем без сети:
  * payload к GitLab формируется верно ({to_project_id});
  * ответ прогоняется через общий `_issue_to_node` → фронт получает ноду с новыми
    project_id/iid и может переложить её в колонку-проект;
  * перенос «в тот же проект» → 400 (без обращения к GitLab);
  * ошибка GitLab (нет прав/нельзя перенести) пробрасывается как HTTPException,
    не валя холст.

Сеть не нужна: подменяем client() фейком, перехватывающим POST (как в
test_issue_detail / test_bulk_patch).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

# изолируем store-БД до импорта пакета (как в остальных тестах)
_TMP_DB = Path("/tmp/issue-graph-move-test.db")
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["ISSUE_GRAPH_DB"] = str(_TMP_DB)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main  # noqa: E402


class _MoveClient:
    """Фейковый клиент: запоминает POST; может вернуть ошибку статуса.

    moved_iid/moved_pid — что отдаёт GitLab после переноса (новый id задачи).
    fail — отдать HTTP 403 (нет прав), чтобы проверить проброс ошибки.
    """

    def __init__(self, moved_pid: int = 20, moved_iid: int = 7, fail: bool = False):
        self.posts: list[tuple[str, dict]] = []
        self.moved_pid = moved_pid
        self.moved_iid = moved_iid
        self.fail = fail

    async def post(self, path, json=None):  # noqa: A002 (имя как в httpx)
        self.posts.append((path, json))
        cl = self

        class _R:
            status_code = 403 if cl.fail else 201
            text = "forbidden" if cl.fail else ""

            def raise_for_status(self_inner):
                if cl.fail:
                    raise httpx.HTTPStatusError(
                        "forbidden",
                        request=httpx.Request("POST", "http://x" + path),
                        response=httpx.Response(403, text="forbidden"),
                    )

            def json(self_inner):
                # GitLab вернул задачу уже в НОВОМ проекте (новые project_id/iid)
                return {
                    "project_id": cl.moved_pid, "iid": cl.moved_iid,
                    "title": "Moved", "state": "opened",
                    "labels": [], "milestone": None, "assignees": [],
                }
        return _R()


def _move(cl: _MoveClient, pid: int, iid: int, to_pid: int):
    """Прогнать move_issue с подменённым client()."""
    orig = main.client
    main.client = lambda: cl  # type: ignore[assignment]
    try:
        return asyncio.run(
            main.move_issue(pid, iid, main.MoveIssue(to_project_id=to_pid)))
    finally:
        main.client = orig  # type: ignore[assignment]


def test_move_payload_and_node():
    cl = _MoveClient(moved_pid=20, moved_iid=7)
    node = _move(cl, 10, 42, 20)
    # один POST на правильный путь с телом {to_project_id}
    assert len(cl.posts) == 1
    path, body = cl.posts[0]
    assert path == "/projects/10/issues/42/move"
    assert body == {"to_project_id": 20}
    # ответ прогнан через _issue_to_node → нода с новым project_id/iid
    assert node["project_id"] == 20
    assert node["iid"] == 7
    assert node["id"] == "20:7"


def test_move_to_same_project_rejected():
    cl = _MoveClient()
    with pytest.raises(main.HTTPException) as ei:
        _move(cl, 10, 42, 10)
    assert ei.value.status_code == 400
    # к GitLab не ходили
    assert cl.posts == []


def test_move_gitlab_error_propagates():
    cl = _MoveClient(fail=True)
    with pytest.raises(main.HTTPException) as ei:
        _move(cl, 10, 42, 20)
    assert ei.value.status_code == 403
    # запрос всё же ушёл (ошибка пришла от GitLab, не до)
    assert len(cl.posts) == 1
