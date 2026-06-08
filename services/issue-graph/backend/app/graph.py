"""Сборка графа issue: ноды = задачи, рёбра = связи между ними.

Работает на любой подписке. На Free доступны только relates_to-связи и
milestones; blocking-связи и epics подтягиваются, если их отдаёт инстанс.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .gitlab_client import Capabilities, GitLabClient, Scope


def _node_id(project_id: int, iid: int) -> str:
    return f"{project_id}:{iid}"


def _issue_to_node(issue: dict) -> dict[str, Any]:
    ms = issue.get("milestone")
    return {
        "id": _node_id(issue["project_id"], issue["iid"]),
        "iid": issue["iid"],
        "project_id": issue["project_id"],
        "title": issue["title"],
        "state": issue["state"],  # opened | closed
        "web_url": issue.get("web_url"),
        "labels": [
            {"name": label_name(la), "color": label_color(la)}
            for la in (issue.get("labels") or [])
        ],
        "milestone": ({"id": ms["id"], "title": ms["title"]} if ms else None),
        "due_date": issue.get("due_date"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "assignees": [
            {"id": a.get("id"), "name": a.get("name"),
             "username": a.get("username"), "avatar_url": a.get("avatar_url")}
            for a in (issue.get("assignees") or [])
        ],
        "weight": issue.get("weight"),
        "type": "issue",
    }


def _issue_detail(issue: dict) -> dict[str, Any]:
    """Детальный объект задачи (Этап 6, паритет с GitLab).

    Расширяет ноду графа полями, которых нет в списочном ответе и которые ценны
    «не выходя в GitLab»: описание (markdown-исходник), вес, time tracking,
    confidential/health_status, start_date, ссылка-референс. Базовые поля берём из
    того же `_issue_to_node`, чтобы карточка и граф не расходились.
    """
    base = _issue_to_node(issue)
    ts = issue.get("time_stats") or {}
    refs = issue.get("references") or {}
    return {
        **base,
        "description": issue.get("description"),
        "confidential": bool(issue.get("confidential")),
        "health_status": issue.get("health_status"),
        "start_date": issue.get("start_date"),
        "reference": refs.get("full") or refs.get("short"),
        "time_stats": {
            "time_estimate": ts.get("time_estimate") or 0,
            "total_time_spent": ts.get("total_time_spent") or 0,
            "human_time_estimate": ts.get("human_time_estimate"),
            "human_total_time_spent": ts.get("human_total_time_spent"),
        },
    }


def label_name(la: Any) -> str:
    return la["name"] if isinstance(la, dict) else str(la)


def label_color(la: Any) -> str | None:
    return la.get("color") if isinstance(la, dict) else None


class GraphBuilder:
    def __init__(self, client: GitLabClient, caps: Capabilities):
        self.client = client
        self.caps = caps

    async def build(
        self, scope: Scope, state: str = "opened",
        assignee: str | None = None,
    ) -> dict[str, Any]:
        # D1: по умолчанию грузим только открытые задачи — меньше N+1 и нод на
        # холсте. Тумблер «открытые / все» на фронте догружает closed (state=all).
        if state not in ("opened", "all"):
            state = "opened"

        # Этап 5: стартовый «Мои задачи» — фильтр assignee на бэке, чтобы N+1 по
        # /links шёл лишь по десяткам своих задач, а не по всей группе. Эпики не
        # фильтруем по исполнителю (у них своя модель) — при assignee их не тянем.
        nodes, edges = await self._collect_nodes_edges(scope, state, assignee)

        # При фильтре по исполнителю эпики не подмешиваем: эпик не «назначен» на
        # человека и притянул бы чужие issue → расширил бы набор сверх «моих».
        if self.caps.epics and not assignee:
            try:
                epic_nodes, epic_edges = await self._collect_epics(scope, set(nodes))
                nodes.update(epic_nodes)
                edges.extend(epic_edges)
            except httpx.HTTPError:
                pass  # лицензия пропала / endpoint недоступен — деградируем тихо

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "meta": {
                "issue_count": sum(1 for n in nodes.values() if n["type"] == "issue"),
                "edge_count": len(edges),
                "capabilities": self.caps.as_dict(),
                "milestones": _distinct_milestones(nodes.values()),
                "labels": _distinct_labels(nodes.values()),
            },
        }

    async def _collect_nodes_edges(
        self, scope: Scope, state: str, assignee: str | None = None
    ) -> tuple[dict[str, dict], list[dict]]:
        """Ноды + рёбра issue↔issue. Быстрый путь — GraphQL, фолбэк — REST.

        Оба пути сводят «сырые» связи в общий `_finalize_edges` (дедуп + store-
        оверлей), поэтому набор рёбер идентичен (паритет, см. тест). При любой
        ошибке GraphQL тихо падаем на REST — обязательный фолбэк для старых CE.
        """
        from .gitlab_graphql import collect_via_graphql, supports_graphql_scope

        if self.caps.graphql and supports_graphql_scope(scope):
            try:
                nodes, raw_links = await collect_via_graphql(
                    self.client, scope, state, assignee)
                node_ids = set(nodes)
                raw_links = [lk for lk in raw_links if lk["tgt"] in node_ids]
                return nodes, _finalize_edges(raw_links)
            except httpx.HTTPError:
                pass  # GraphQL недоступен/частичные ошибки — фолбэк на REST

        return await self._collect_rest(scope, state, assignee)

    async def _collect_rest(
        self, scope: Scope, state: str, assignee: str | None = None
    ) -> tuple[dict[str, dict], list[dict]]:
        """REST-сборка: список issues + N+1 по /links (фолбэк-путь)."""
        params: dict[str, Any] = {
            "scope": "all", "state": state, "with_labels_details": "true",
        }
        if assignee:
            # «Мои задачи» (Этап 5): GitLab фильтрует список по исполнителю →
            # N+1 по /links идёт лишь по своим задачам, а не по всей группе.
            params["assignee_username"] = assignee
        issues = await self.client.paginate(scope.issues_path, params=params)
        nodes = {_node_id(i["project_id"], i["iid"]): _issue_to_node(i)
                 for i in issues}
        raw_links = await self._collect_raw_links(issues, set(nodes))
        return nodes, _finalize_edges(raw_links)

    async def _collect_raw_links(
        self, issues: list[dict], node_ids: set[str]
    ) -> list[dict]:
        """Сырые связи issue↔issue из REST /links (до дедупа/оверлея)."""
        async def fetch(issue: dict) -> list[dict]:
            pid, iid = issue["project_id"], issue["iid"]
            try:
                linked = await self.client.get_json(
                    f"/projects/{pid}/issues/{iid}/links"
                )
            except httpx.HTTPError:
                return []
            src = _node_id(pid, iid)
            out = []
            for lk in linked:
                tgt = _node_id(lk["project_id"], lk["iid"])
                if tgt not in node_ids:
                    continue
                out.append({"src": src, "tgt": tgt,
                            "link_type": lk.get("link_type", "relates_to"),
                            "link_id": lk.get("issue_link_id")})
            return out

        raw = await asyncio.gather(*(fetch(i) for i in issues))
        return [lk for sub in raw for lk in sub]

    async def _collect_epics(
        self, scope: Scope, node_ids: set[str]
    ) -> tuple[dict[str, dict], list[dict]]:
        """Epics (Premium+): добавляем эпики как ноды и рёбра epic→issue."""
        group_id = scope.id if scope.kind == "group" else None
        if not group_id:
            return {}, []
        epics = await self.client.paginate(f"/groups/{group_id}/epics",
                                           params={"state": "all"})
        epic_nodes: dict[str, dict] = {}
        for ep in epics:
            eid = f"epic:{ep['id']}"
            epic_nodes[eid] = {
                "id": eid, "iid": ep["iid"], "project_id": None,
                "title": ep["title"], "state": ep["state"],
                "web_url": ep.get("web_url"), "labels": [],
                "milestone": None, "due_date": ep.get("due_date"),
                "created_at": ep.get("created_at"),
                "updated_at": ep.get("updated_at"),
                "assignees": [], "weight": None,
                "type": "epic",
            }

        # Этап 3: пагинации issues по эпикам — параллельно (раньше шли серией,
        # E последовательных round-trip). Конкурентность ограничивает семафор
        # клиента; порядок сохраняем через gather, чтобы рёбра были детерминированы.
        async def epic_issues(ep: dict) -> list[dict]:
            return await self.client.paginate(
                f"/groups/{group_id}/epics/{ep['iid']}/issues"
            )

        per_epic = await asyncio.gather(*(epic_issues(ep) for ep in epics))

        epic_edges: list[dict] = []
        for ep, issues_in in zip(epics, per_epic):
            eid = f"epic:{ep['id']}"
            for iss in issues_in:
                tgt = _node_id(iss["project_id"], iss["iid"])
                if tgt in node_ids:
                    epic_edges.append({"id": f"{eid}->{tgt}", "source": eid,
                                       "target": tgt, "type": "epic"})
        return epic_nodes, epic_edges


def _finalize_edges(raw_links: list[dict]) -> list[dict]:
    """Единая финализация рёбер для REST и GraphQL: дедуп + store-оверлей.

    Один источник правды по маппингу рёбер → паритет путей гарантирован
    конструктивно (тест паритета это проверяет на фикстурах).
    """
    from . import store
    return _dedupe_edges(raw_links, store.get_links())


def _dedupe_edges(raw: list[dict],
                  overlay: dict[str, dict] | None = None) -> list[dict]:
    """Одно ребро на пару. Поверх симметричной GitLab relates_to накладываем
    наш тип/направление (blocks / parent), если он задан в store."""
    overlay = overlay or {}
    seen: dict[str, dict] = {}
    for lk in raw:
        src, tgt, lt = lk["src"], lk["tgt"], lk["link_type"]
        directed = False
        if lt == "is_blocked_by":      # нативный blocks (Premium) — нормализуем
            src, tgt, lt = tgt, src, "blocks"
        if lt == "blocks":
            key = f"blocks|{src}->{tgt}"
            directed = True
        else:  # relates_to — смотрим наш оверлей
            a, b = sorted([src, tgt])
            key = f"relates|{a}|{b}"
            ov = overlay.get("|".join([a, b]))
            if ov:  # наш тип/направление
                src, tgt = ov["source"], ov["target"]
                lt = "blocks" if ov["kind"] == "blocks" else (
                    "parent" if ov["kind"] == "parent" else "relates_to")
                directed = ov["kind"] in ("blocks", "parent")
            else:
                src, tgt, lt = a, b, "relates_to"
        seen[key] = {"id": key, "source": src, "target": tgt, "type": lt,
                     "directed": directed, "link_id": lk.get("link_id")}
    return list(seen.values())


def _distinct_milestones(nodes) -> list[dict]:
    out: dict[Any, dict] = {}
    for n in nodes:
        ms = n.get("milestone")
        if ms:
            out[ms["id"]] = ms
    return list(out.values())


def _distinct_labels(nodes) -> list[dict]:
    out: dict[str, dict] = {}
    for n in nodes:
        for la in n.get("labels", []):
            out[la["name"]] = la
    return list(out.values())
