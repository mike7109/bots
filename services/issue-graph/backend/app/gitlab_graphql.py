"""GraphQL-сборщик графа issue — быстрый путь вместо REST N+1.

Зачем: REST-сборка делает отдельный `GET /links` на каждую задачу
(`graph.py:_collect_raw_links`) — на сотнях задач это сотни round-trip и
доминанта времени холодной загрузки. GraphQL отдаёт issues вместе со связями
(`linkedItems`), метками, исполнителями и спринтом одним запросом с keyset-
пагинацией → ~3-6 запросов вместо ~300.

КРИТИЧНО (паритет данных): рёбра/ноды строятся ТЕМ ЖЕ маппером, что и REST
(`graph._issue_to_node`, `graph._dedupe_edges`). `linkedItems.linkType` имеет тот
же словарь, что REST `link_type` (`relates_to | blocks | is_blocked_by`), а каждая
связь приходит с обеих сторон — ровно как у REST `/links`. Поэтому GraphQL-сборщик
лишь приводит ответ к «сырому» виду REST (`{src, tgt, link_type, link_id}`) и
отдаёт его в общий `_dedupe_edges`. Это гарантирует идентичный набор рёбер
(тип + направление + store-оверлей) — см. тест паритета.

Доступность определяется флагом `caps.graphql`; при его отсутствии (старые CE,
GraphQL выключен) `GraphBuilder.build` обязан падать на REST-фолбэк.
"""
from __future__ import annotations

from typing import Any

import httpx

from .gitlab_client import GitLabClient, Scope

# Запрос issues группы/проекта со связями, метками, исполнителями и спринтом.
# linkedItems отдаёт связи прямо в issue — убирает REST N+1 по /links.
_ISSUE_FIELDS = """
        iid
        title
        state
        webUrl
        dueDate
        createdAt
        updatedAt
        weight
        project { id }
        milestone { id title }
        labels { nodes { title color } }
        assignees { nodes { id name username avatarUrl } }
        linkedItems {
          nodes {
            linkType
            issue { iid project { id } webUrl }
          }
        }
"""

_GROUP_QUERY = """
query($fullPath: ID!, $state: IssuableState, $assignees: [String!], $after: String) {
  group(fullPath: $fullPath) {
    issues(includeSubgroups: true, state: $state, assigneeUsernames: $assignees,
           first: 100, after: $after) {
      pageInfo { endCursor hasNextPage }
      nodes {
%s
      }
    }
  }
}
""" % _ISSUE_FIELDS

_PROJECT_QUERY = """
query($fullPath: ID!, $state: IssuableState, $assignees: [String!], $after: String) {
  project(fullPath: $fullPath) {
    issues(state: $state, assigneeUsernames: $assignees, first: 100, after: $after) {
      pageInfo { endCursor hasNextPage }
      nodes {
%s
      }
    }
  }
}
""" % _ISSUE_FIELDS

# REST state → GraphQL IssuableState. "all" в GraphQL = отсутствие фильтра.
_STATE_GQL = {"opened": "opened", "closed": "closed"}


def supports_graphql_scope(scope: Scope) -> bool:
    """GraphQL `group/project(fullPath:)` принимает только текстовый путь, не id.

    Если scope задан числовым id (`scope-meta`/namespaces отдают и id, и full_path,
    но в граф может прийти id) — текущий REST-фолбэк остаётся единственным путём.
    """
    return not scope.id.isdigit()


async def probe_graphql(client: GitLabClient) -> bool:
    """Пробный POST /api/graphql {currentUser{id}}: True при 200 без errors.

    Сетевые/HTTP-ошибки → False (деградируем на REST), наружу не пробрасываем.
    """
    try:
        data = await _post_graphql(client, "{ currentUser { id } }", {})
    except httpx.HTTPError:
        return False
    return isinstance(data, dict) and not data.get("errors")


async def _post_graphql(client: GitLabClient, query: str,
                        variables: dict[str, Any]) -> dict[str, Any]:
    """POST {base}/api/graphql. graphql живёт вне /api/v4 — бьём напрямую."""
    # client.base = ".../api/v4"; graphql — соседний путь ".../api/graphql".
    url = client.base.rsplit("/api/v4", 1)[0] + "/api/graphql"
    async with client._sem:  # тот же семафор конкурентности, что и у REST
        r = await client._client.post(url, json={"query": query,
                                                 "variables": variables})
    # 401 на OAuth — даём общему request-пути обновить токен нельзя (другой url),
    # поэтому на любой не-2xx считаем GraphQL недоступным и фолбэчимся на REST.
    r.raise_for_status()
    return r.json()


def _gql_id_to_int(gid: Any) -> Any:
    """GraphQL отдаёт глобальные id ("gid://gitlab/User/42"); тянем хвостовой int."""
    if isinstance(gid, str) and "/" in gid:
        tail = gid.rsplit("/", 1)[-1]
        return int(tail) if tail.isdigit() else gid
    return gid


def _node_from_gql(issue: dict[str, Any]) -> dict[str, Any]:
    """GraphQL issue → REST-подобный dict для общего graph._issue_to_node.

    Приводим имена полей (camelCase, *.nodes) к REST-форме, чтобы переиспользовать
    единый маппер и не плодить вторую копию логики ноды.
    """
    ms = issue.get("milestone")
    return {
        "iid": issue["iid"],
        # GraphQL у Issue нет скалярного projectId — берём из вложенного project{id}
        # (глобальный gid → int). См. _ISSUE_FIELDS.
        "project_id": _gql_id_to_int((issue.get("project") or {}).get("id")),
        "title": issue["title"],
        "state": issue["state"],
        "web_url": issue.get("webUrl"),
        "labels": [
            {"name": la["title"], "color": la.get("color")}
            for la in (issue.get("labels") or {}).get("nodes", [])
        ],
        "milestone": ({"id": _gql_id_to_int(ms["id"]), "title": ms["title"]}
                      if ms else None),
        "due_date": issue.get("dueDate"),
        "created_at": issue.get("createdAt"),
        "updated_at": issue.get("updatedAt"),
        "assignees": [
            {"id": _gql_id_to_int(a.get("id")), "name": a.get("name"),
             "username": a.get("username"), "avatar_url": a.get("avatarUrl")}
            for a in (issue.get("assignees") or {}).get("nodes", [])
        ],
        "weight": issue.get("weight"),
    }


def _raw_links_from_gql(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """linkedItems issue → «сырые» рёбра REST-формы для общего _dedupe_edges.

    linkType ∈ {relates_to, blocks, is_blocked_by} — тот же словарь, что REST
    link_type. issue.project.id + iid дают target-ноду. link_id GraphQL не отдаёт
    стабильно (нет issue_link_id в linkedItems) → None, как и при недоступном поле.
    """
    pid = _gql_id_to_int((issue.get("project") or {}).get("id"))
    src = f"{pid}:{issue['iid']}"
    out: list[dict[str, Any]] = []
    for li in (issue.get("linkedItems") or {}).get("nodes", []):
        other = li.get("issue") or {}
        opid = _gql_id_to_int((other.get("project") or {}).get("id"))
        if opid is None or other.get("iid") is None:
            continue
        out.append({
            "src": src,
            "tgt": f"{opid}:{other['iid']}",
            "link_type": li.get("linkType") or "relates_to",
            "link_id": None,
        })
    return out


async def _paginate_issues(client: GitLabClient, query: str, root_key: str,
                           variables: dict[str, Any]) -> list[dict[str, Any]]:
    """Keyset-пагинация по pageInfo.endCursor одного scope."""
    issues: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        v = dict(variables, after=after)
        data = await _post_graphql(client, query, v)
        if data.get("errors"):
            # частичные ошибки GraphQL — считаем путь ненадёжным, наверх HTTPError
            raise httpx.HTTPError(f"graphql errors: {data['errors'][:1]}")
        root = (data.get("data") or {}).get(root_key)
        if not root:
            break
        conn = root.get("issues") or {}
        issues.extend(conn.get("nodes") or [])
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
        if not after:
            break
    return issues


async def collect_via_graphql(
    client: GitLabClient, scope: Scope, state: str,
    assignee: str | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """Собрать ноды и «сырые» рёбра через GraphQL.

    Возвращает (nodes_by_id, raw_links) — БЕЗ дедупа/оверлея: финальный маппинг
    рёбер делает общий graph._dedupe_edges (паритет с REST). Бросает httpx.HTTPError
    при недоступности — вызывающий код обязан упасть на REST.
    """
    from .graph import _issue_to_node, _node_id  # локально: избегаем цикла импорта

    query = _GROUP_QUERY if scope.kind == "group" else _PROJECT_QUERY
    root_key = "group" if scope.kind == "group" else "project"
    variables: dict[str, Any] = {"fullPath": scope.id}
    gql_state = _STATE_GQL.get(state)
    if gql_state:  # "all" → не передаём фильтр (берём все состояния)
        variables["state"] = gql_state
    if assignee:  # Этап 5: «Мои задачи» — фильтр по исполнителю на стороне GitLab
        variables["assignees"] = [assignee]

    raw_issues = await _paginate_issues(client, query, root_key, variables)

    nodes: dict[str, dict] = {}
    raw_links: list[dict] = []
    for gi in raw_issues:
        rest_issue = _node_from_gql(gi)
        nid = _node_id(rest_issue["project_id"], rest_issue["iid"])
        nodes[nid] = _issue_to_node(rest_issue)
        raw_links.extend(_raw_links_from_gql(gi))

    return nodes, raw_links
