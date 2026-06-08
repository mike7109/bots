"""Тесты деталей задачи и инлайн-правок (Этап 6 ROADMAP) — без сети.

Этап 6 «паритет с GitLab» зависит от живого GitLab, поэтому покрываем без сети:
  * `_issue_detail` — маппинг детального issue (description, weight, time_stats,
    confidential, health_status, start_date, reference) поверх общей ноды графа;
  * `_merge_merge_requests` — объединение related + closed_by в один список без
    дублей с флагом `closes`;
  * PATCH issue с инлайн-метками/весом/описанием — payload к GitLab формируется
    верно (add_labels/remove_labels инкрементальны, weight=null снимает).

Сеть не нужна: маппер — чистая функция; PATCH проверяем через фейковый клиент,
перехватывающий тело запроса (как в test_graph_parity/test_swr_cache).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# изолируем store-БД до импорта пакета (как в остальных тестах)
_TMP_DB = Path("/tmp/issue-graph-detail-test.db")
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["ISSUE_GRAPH_DB"] = str(_TMP_DB)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main  # noqa: E402
from app.graph import _issue_detail  # noqa: E402


# ── _issue_detail: маппинг детального объекта ────────────────────────────────
def _sample_issue() -> dict:
    return {
        "project_id": 10,
        "iid": 42,
        "title": "Detailed issue",
        "state": "opened",
        "web_url": "https://gl/x/-/issues/42",
        "labels": [{"name": "bug", "color": "#d00"}],
        "milestone": {"id": 5, "title": "Sprint 1"},
        "due_date": "2026-07-01",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-02T00:00:00Z",
        "assignees": [
            {"id": 1, "name": "Alice", "username": "alice", "avatar_url": None},
            {"id": 2, "name": "Bob", "username": "bob", "avatar_url": None},
        ],
        "weight": 3,
        "description": "## Heading\n\n- item **bold**",
        "confidential": True,
        "health_status": "on_track",
        "start_date": "2026-06-10",
        "references": {"full": "x#42", "short": "#42"},
        "time_stats": {
            "time_estimate": 7200,
            "total_time_spent": 3600,
            "human_time_estimate": "2h",
            "human_total_time_spent": "1h",
        },
    }


def test_issue_detail_carries_extra_fields():
    d = _issue_detail(_sample_issue())
    # базовые поля ноды на месте (общий маппер)
    assert d["id"] == "10:42"
    assert d["title"] == "Detailed issue"
    assert len(d["assignees"]) == 2  # несколько исполнителей
    # детальные поля Этапа 6
    assert d["description"] == "## Heading\n\n- item **bold**"
    assert d["weight"] == 3
    assert d["confidential"] is True
    assert d["health_status"] == "on_track"
    assert d["start_date"] == "2026-06-10"
    assert d["reference"] == "x#42"
    assert d["time_stats"]["time_estimate"] == 7200
    assert d["time_stats"]["human_total_time_spent"] == "1h"


def test_issue_detail_graceful_when_fields_absent():
    """Старый CE / Free не отдаёт часть полей → дефолты, без падений."""
    minimal = {
        "project_id": 1, "iid": 1, "title": "T", "state": "opened",
    }
    d = _issue_detail(minimal)
    assert d["description"] is None
    assert d["confidential"] is False
    assert d["health_status"] is None
    assert d["start_date"] is None
    assert d["reference"] is None
    # time_stats всегда числовой каркас, даже если поля нет
    assert d["time_stats"]["time_estimate"] == 0
    assert d["time_stats"]["total_time_spent"] == 0


# ── _merge_merge_requests: дедуп related + closed_by, флаг closes ────────────
def test_merge_mrs_dedup_and_closes_flag():
    related = [
        {"id": 100, "iid": 1, "title": "rel", "state": "opened",
         "web_url": "u1", "references": {"full": "x!1"}},
        {"id": 200, "iid": 2, "title": "both", "state": "merged",
         "web_url": "u2", "references": {"full": "x!2"}},
    ]
    closed_by = [
        {"id": 200, "iid": 2, "title": "both", "state": "merged",
         "web_url": "u2", "references": {"full": "x!2"}},  # дубль related
        {"id": 300, "iid": 3, "title": "closer", "state": "opened",
         "web_url": "u3", "references": {"full": "x!3"}},
    ]
    out = main._merge_merge_requests(related, closed_by)
    by_id = {mr["id"]: mr for mr in out}
    assert set(by_id) == {100, 200, 300}  # дедуп: 200 один раз
    assert by_id[100]["closes"] is False    # только related
    assert by_id[200]["closes"] is True     # в обоих → closes
    assert by_id[300]["closes"] is True     # только closed_by
    assert by_id[100]["reference"] == "x!1"


def test_merge_mrs_empty():
    assert main._merge_merge_requests([], []) == []


# ── PATCH issue: инлайн-метки/вес/описание → корректный payload к GitLab ─────
class _CapturingClient:
    """Фейковый клиент: запоминает json последнего PUT, не ходит в сеть."""
    def __init__(self):
        self.last_put_json: dict | None = None

    async def put(self, path, json=None):  # noqa: A002 (имя как в httpx)
        self.last_put_json = json

        class _R:
            status_code = 200

            def json(self_inner):
                # PUT возвращает обновлённый issue → прогоняем через _issue_to_node
                return {
                    "project_id": 10, "iid": 42, "title": "T", "state": "opened",
                    "labels": [], "milestone": None, "assignees": [],
                }
        return _R()


def _patch(monkeypatch_client: _CapturingClient, body: main.IssuePatch):
    """Прогнать patch_issue с подменённым client()."""
    orig = main.client
    main.client = lambda: monkeypatch_client  # type: ignore[assignment]
    try:
        # _invalidate_cache работает по сессии; без сессии — no-op, нам ок
        return asyncio.run(main.patch_issue(10, 42, body))
    finally:
        main.client = orig  # type: ignore[assignment]


def test_patch_add_remove_labels():
    cl = _CapturingClient()
    _patch(cl, main.IssuePatch(add_labels=["bug", "p1"], remove_labels=["wip"]))
    assert cl.last_put_json["add_labels"] == "bug,p1"
    assert cl.last_put_json["remove_labels"] == "wip"
    # неуказанные поля в payload не попадают
    assert "assignee_ids" not in cl.last_put_json
    assert "milestone_id" not in cl.last_put_json


def test_patch_weight_set_and_clear():
    cl = _CapturingClient()
    _patch(cl, main.IssuePatch(weight=5))
    assert cl.last_put_json["weight"] == 5
    # null снимает вес → пустая строка для GitLab
    cl2 = _CapturingClient()
    _patch(cl2, main.IssuePatch(weight=None))
    assert cl2.last_put_json["weight"] == ""


def test_patch_description():
    cl = _CapturingClient()
    _patch(cl, main.IssuePatch(description="# new body"))
    assert cl.last_put_json["description"] == "# new body"


def test_patch_empty_raises():
    cl = _CapturingClient()
    with pytest.raises(main.HTTPException):
        _patch(cl, main.IssuePatch())
