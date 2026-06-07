"""Тест паритета связей: REST-путь vs GraphQL-путь GraphBuilder.

Этап 3 (ROADMAP). Риск ДАННЫХ: GraphQL и REST по-разному именуют поля и порядок
src/tgt, а поверх симметричной relates_to накладывается store-оверлей
(blocks/parent) с направлением. Если маппинг разойдётся — сломаются стрелки и
пользовательские оверлеи. Здесь проверяем, что НАБОР рёбер (id + источник +
цель + тип + направление) ИДЕНТИЧЕН на обоих путях, на одной фикстуре, без сети.

Сети нет: оба пути ходят в фейковый клиент, который отдаёт одни и те же данные
в REST-форме (/links) и в GraphQL-форме (linkedItems) из общего сидинга.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Изолируем store: своя временная БД ДО импорта app.store (DB_PATH читается из env).
_TMP_DB = Path("/tmp/issue-graph-parity-test.db")
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["ISSUE_GRAPH_DB"] = str(_TMP_DB)

# Импортируем пакет app по относительному пути backend/, без установки.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import store                       # noqa: E402
from app.gitlab_client import Capabilities, Scope  # noqa: E402
from app.graph import GraphBuilder          # noqa: E402


# ── сидинг: задачи и связи в нейтральной форме ──────────────────────────────
# Задачи: (project_id, iid). Полный node-id = "pid:iid".
ISSUES = [
    {"project_id": 10, "iid": 1, "title": "A", "state": "opened"},
    {"project_id": 10, "iid": 2, "title": "B", "state": "opened"},
    {"project_id": 10, "iid": 3, "title": "C", "state": "opened"},
    {"project_id": 10, "iid": 4, "title": "D", "state": "opened"},
    {"project_id": 20, "iid": 7, "title": "E", "state": "opened"},  # другой проект
    {"project_id": 10, "iid": 9, "title": "Z", "state": "opened"},  # без связей
]

# Связи в «направленной» форме источника: (src_pid, src_iid, tgt_pid, tgt_iid, lt).
# lt — как его отдаёт GitLab С ТОЧКИ ЗРЕНИЯ src-задачи (relates_to/blocks/is_blocked_by).
# Каждая связь видна с ОБЕИХ сторон (как у REST /links и GraphQL linkedItems) —
# обратную сторону мы порождаем автоматически (важный кейс дедупа).
LINKS = [
    # симметричная relates 1<->2
    (10, 1, 10, 2, "relates_to"),
    # нативный blocks: 1 блокирует 3 (Premium)
    (10, 1, 10, 3, "blocks"),
    # is_blocked_by: 4 заблокирована 2  → должно нормализоваться в blocks 2->4
    (10, 4, 10, 2, "is_blocked_by"),
    # кросс-проектная relates 3 <-> 20:7
    (10, 3, 20, 7, "relates_to"),
    # relates 2 <-> 3 — на неё навесим store-оверлей parent (3 — родитель 2)
    (10, 2, 10, 3, "relates_to"),
]


def _other_side(lt: str) -> str:
    """Как та же связь выглядит со стороны target-задачи."""
    if lt == "blocks":
        return "is_blocked_by"
    if lt == "is_blocked_by":
        return "blocks"
    return "relates_to"


def _node_id(pid: int, iid: int) -> str:
    return f"{pid}:{iid}"


# Построить полный список связей с ОБЕИХ сторон (как реально отдаёт GitLab).
def _bidirectional_links() -> dict[tuple[int, int], list[dict]]:
    """ {(pid,iid): [ {tgt_pid,tgt_iid,link_type}, ... ] } — связи каждой issue."""
    by_issue: dict[tuple[int, int], list[dict]] = {
        (i["project_id"], i["iid"]): [] for i in ISSUES
    }
    for spid, siid, tpid, tiid, lt in LINKS:
        by_issue[(spid, siid)].append(
            {"tgt_pid": tpid, "tgt_iid": tiid, "link_type": lt})
        by_issue[(tpid, tiid)].append(
            {"tgt_pid": spid, "tgt_iid": siid, "link_type": _other_side(lt)})
    return by_issue


_LINKS_BY_ISSUE = _bidirectional_links()


# ── фейковый клиент: одни и те же данные в REST- и GraphQL-форме ────────────
class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeHttpx:
    """Имитация client._client: .post(url, json=...) → GraphQL-ответ."""
    def __init__(self, owner: "FakeClient"):
        self._owner = owner

    async def post(self, url, json=None):  # noqa: A002 (имя как в httpx)
        return FakeResp(self._owner._graphql(json or {}))


class FakeSem:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class FakeClient:
    """Утиная типизация GitLabClient для обоих путей сборки, без сети/httpx-клиента.

    REST: paginate(issues_path) + get_json(/projects/.../links).
    GraphQL: _post_graphql → client.base + client._sem + client._client.post.
    """
    def __init__(self):
        self.base = "https://gl.example/api/v4"
        self._sem = FakeSem()
        self._client = _FakeHttpx(self)
        # Этап 5: фиксируем переданные фильтры обоих путей для теста «Мои задачи»
        self.last_rest_params: dict | None = None
        self.last_gql_variables: dict | None = None

    # — REST —
    async def paginate(self, path, params=None):
        if path.endswith("/issues"):
            self.last_rest_params = dict(params or {})
            return [
                {**i, "web_url": None, "labels": [], "milestone": None,
                 "due_date": None, "created_at": None, "updated_at": None,
                 "assignees": [], "weight": None}
                for i in ISSUES
            ]
        return []

    async def get_json(self, path, params=None):
        # /projects/{pid}/issues/{iid}/links
        parts = path.strip("/").split("/")
        pid, iid = int(parts[1]), int(parts[3])
        out = []
        for lk in _LINKS_BY_ISSUE.get((pid, iid), []):
            out.append({
                "project_id": lk["tgt_pid"], "iid": lk["tgt_iid"],
                "link_type": lk["link_type"], "issue_link_id": None,
            })
        return out

    # — GraphQL —
    def _graphql(self, body: dict):
        q = body.get("query", "")
        if "currentUser" in q:
            return {"data": {"currentUser": {"id": "gid://gitlab/User/1"}}}
        self.last_gql_variables = dict(body.get("variables") or {})
        root_key = "group" if "group(" in q else "project"
        nodes = []
        for i in ISSUES:
            pid, iid = i["project_id"], i["iid"]
            # реальная форма запроса: project { id } с глобальным gid (не projectId)
            linked_nodes = [
                {"linkType": lk["link_type"],
                 "issue": {"iid": lk["tgt_iid"],
                           "project": {"id": f"gid://gitlab/Project/{lk['tgt_pid']}"},
                           "webUrl": None}}
                for lk in _LINKS_BY_ISSUE.get((pid, iid), [])
            ]
            nodes.append({
                "iid": iid, "title": i["title"], "state": i["state"],
                "webUrl": None, "dueDate": None, "createdAt": None,
                "updatedAt": None, "weight": None, "milestone": None,
                "project": {"id": f"gid://gitlab/Project/{pid}"},
                "labels": {"nodes": []},
                "assignees": {"nodes": []},
                "linkedItems": {"nodes": linked_nodes},
            })
        return {"data": {root_key: {
            "issues": {
                "pageInfo": {"endCursor": None, "hasNextPage": False},
                "nodes": nodes,
            }
        }}}


def _edge_key(e: dict) -> tuple:
    """Сравниваемая сигнатура ребра: id+источник+цель+тип+направление."""
    return (e["id"], e["source"], e["target"], e["type"], bool(e.get("directed")))


def _edge_set(edges: list[dict]) -> set[tuple]:
    return {_edge_key(e) for e in edges}


async def _build(graphql_enabled: bool) -> dict:
    caps = Capabilities(graphql=graphql_enabled)
    builder = GraphBuilder(FakeClient(), caps)
    # full_path-scope (не числовой) — иначе GraphQL отключается по supports_graphql_scope
    return await builder.build(Scope("group", "acme/team"), state="opened")


# ── тесты ───────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clean_store():
    """Чистый store перед каждым тестом + наполняем оверлеями сидинга."""
    for src, tgt in list(_all_pairs()):
        store.delete_link(src, tgt)
    yield
    for src, tgt in list(_all_pairs()):
        store.delete_link(src, tgt)


def _all_pairs():
    ids = [_node_id(i["project_id"], i["iid"]) for i in ISSUES]
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            yield ids[a], ids[b]


def test_parity_without_overlay():
    """Без store-оверлея набор рёбер REST == GraphQL."""
    rest = asyncio.run(_build(graphql_enabled=False))
    gql = asyncio.run(_build(graphql_enabled=True))

    assert _edge_set(rest["edges"]) == _edge_set(gql["edges"])
    # ноды тоже совпадают по id
    assert {n["id"] for n in rest["nodes"]} == {n["id"] for n in gql["nodes"]}


def test_parity_with_store_overlay():
    """С оверлеем parent/blocks направление и тип совпадают на обоих путях."""
    # 2<->3 relates → parent: 3 родитель 2
    store.set_link("parent", _node_id(10, 3), _node_id(10, 2))
    # 3<->20:7 relates → blocks: 3 блокирует 20:7
    store.set_link("blocks", _node_id(10, 3), _node_id(20, 7))

    rest = asyncio.run(_build(graphql_enabled=False))
    gql = asyncio.run(_build(graphql_enabled=True))

    assert _edge_set(rest["edges"]) == _edge_set(gql["edges"])

    # sanity: оверлей реально применился (есть parent и наш blocks с направлением)
    by_id = {e["id"]: e for e in gql["edges"]}
    parent = by_id.get("relates|10:2|10:3")
    assert parent is not None and parent["type"] == "parent"
    assert parent["source"] == _node_id(10, 3) and parent["target"] == _node_id(10, 2)
    assert parent["directed"] is True


def test_parity_native_blocks_and_is_blocked_by_normalised():
    """Нативный blocks и обратный is_blocked_by нормализуются одинаково."""
    rest = asyncio.run(_build(graphql_enabled=False))
    gql = asyncio.run(_build(graphql_enabled=True))

    assert _edge_set(rest["edges"]) == _edge_set(gql["edges"])

    by_id = {e["id"]: e for e in rest["edges"]}
    # 1 blocks 3 → одно направленное ребро
    b13 = by_id.get("blocks|10:1->10:3")
    assert b13 is not None and b13["directed"] is True
    assert b13["source"] == _node_id(10, 1) and b13["target"] == _node_id(10, 3)
    # 4 is_blocked_by 2 → нормализовано в blocks 2->4
    b24 = by_id.get("blocks|10:2->10:4")
    assert b24 is not None and b24["directed"] is True
    assert b24["source"] == _node_id(10, 2) and b24["target"] == _node_id(10, 4)


def test_graphql_fallback_to_rest_on_error():
    """Если GraphQL падает (HTTPError), build обязан собрать через REST."""
    import httpx

    caps = Capabilities(graphql=True)
    client = FakeClient()

    # ломаем только GraphQL-путь; REST остаётся рабочим
    async def boom(*a, **kw):
        raise httpx.HTTPError("graphql down")

    # collect_via_graphql импортируется внутри метода из app.gitlab_graphql — патчим там
    from app import gitlab_graphql
    gitlab_graphql.collect_via_graphql = boom  # type: ignore[assignment]
    try:
        data = asyncio.run(GraphBuilder(client, caps).build(
            Scope("group", "acme/team"), state="opened"))
    finally:
        # восстанавливаем оригинал, чтобы не течь между тестами
        import importlib
        importlib.reload(gitlab_graphql)

    # REST-фолбэк дал ноды и рёбра
    assert len(data["nodes"]) == len(ISSUES)
    assert len(data["edges"]) > 0


def test_graphql_skipped_for_numeric_scope():
    """Числовой id scope → GraphQL пропускается (fullPath нужен текстовый)."""
    from app.gitlab_graphql import supports_graphql_scope
    assert supports_graphql_scope(Scope("group", "acme/team")) is True
    assert supports_graphql_scope(Scope("group", "123")) is False
    assert supports_graphql_scope(Scope("project", "456")) is False


# ── Этап 5: «Мои задачи» — фильтр assignee прокидывается на бэк (оба пути) ────
def test_assignee_filter_threaded_to_rest():
    """REST-сборка передаёт assignee_username в параметры списка issues."""
    caps = Capabilities(graphql=False)
    client = FakeClient()
    asyncio.run(GraphBuilder(client, caps).build(
        Scope("group", "acme/team"), state="opened", assignee="alice"))
    assert client.last_rest_params is not None
    assert client.last_rest_params.get("assignee_username") == "alice"


def test_assignee_filter_threaded_to_graphql():
    """GraphQL-сборка передаёт assigneeUsernames в variables."""
    caps = Capabilities(graphql=True)
    client = FakeClient()
    asyncio.run(GraphBuilder(client, caps).build(
        Scope("group", "acme/team"), state="opened", assignee="alice"))
    assert client.last_gql_variables is not None
    assert client.last_gql_variables.get("assignees") == ["alice"]


def test_no_assignee_filter_by_default():
    """Без assignee фильтр в запрос не уходит (обычный полный вид группы)."""
    caps = Capabilities(graphql=False)
    client = FakeClient()
    asyncio.run(GraphBuilder(client, caps).build(
        Scope("group", "acme/team"), state="opened"))
    assert client.last_rest_params is not None
    assert "assignee_username" not in client.last_rest_params
