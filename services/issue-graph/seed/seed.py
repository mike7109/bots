#!/usr/bin/env python3
"""Засев тестового GitLab данными для issue-graph.

Создаёт группу graphlab, 3 проекта, пользователей, метки, milestones (роль
"спринтов"/целей на Free-tier) и десятки связанных issue. Идемпотентно:
повторный запуск переиспользует уже созданные сущности по имени/пути.

Запуск:
    GITLAB_URL=http://localhost:8929 GITLAB_TOKEN=... python seed.py
"""
from __future__ import annotations

import os
import sys
from urllib.parse import quote

import httpx

URL = os.environ.get("GITLAB_URL", "http://localhost:8929").rstrip("/")
TOKEN = os.environ.get("GITLAB_TOKEN") or sys.exit("GITLAB_TOKEN required")
API = URL + "/api/v4"
GROUP = "graphlab"

c = httpx.Client(headers={"PRIVATE-TOKEN": TOKEN}, timeout=60.0)


def api(method: str, path: str, **kw) -> httpx.Response:
    return c.request(method, API + path, **kw)


def ok(r: httpx.Response) -> dict:
    if r.status_code >= 300:
        raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def find_group(path: str) -> dict | None:
    r = api("GET", f"/groups/{quote(path, safe='')}")
    return r.json() if r.status_code == 200 else None


def ensure_group() -> dict:
    g = find_group(GROUP)
    if g:
        print(f"group {GROUP} exists (id={g['id']})")
        return g
    g = ok(api("POST", "/groups", json={
        "name": "GraphLab", "path": GROUP,
        "description": "Демо-группа для issue-graph"}))
    print(f"created group {GROUP} (id={g['id']})")
    return g


def ensure_project(group_id: int, path: str, name: str) -> dict:
    full = f"{GROUP}/{path}"
    r = api("GET", f"/projects/{quote(full, safe='')}")
    if r.status_code == 200:
        print(f"  project {full} exists")
        return r.json()
    p = ok(api("POST", "/projects", json={
        "name": name, "path": path, "namespace_id": group_id,
        "issues_access_level": "enabled", "initialize_with_readme": True}))
    print(f"  created project {full} (id={p['id']})")
    return p


def ensure_user(username: str, name: str) -> dict:
    r = api("GET", "/users", params={"username": username})
    found = r.json() if r.status_code == 200 else []
    if found:
        return found[0]
    u = ok(api("POST", "/users", json={
        "username": username, "name": name, "email": f"{username}@graphlab.test",
        "password": "Demo!Pass123", "skip_confirmation": True}))
    print(f"  created user {username} (id={u['id']})")
    return u


def ensure_member(group_id: int, user_id: int) -> None:
    api("POST", f"/groups/{group_id}/members",
        json={"user_id": user_id, "access_level": 40})  # 40 = maintainer


def ensure_label(project_id: int, name: str, color: str) -> None:
    """Создать метку на уровне ПРОЕКТА (идемпотентно — 409 при существующей)."""
    api("POST", f"/projects/{project_id}/labels", json={"name": name, "color": color})


def ensure_group_label(group_id: int, name: str, color: str) -> dict:
    """Создать/найти метку на уровне ГРУППЫ (идемпотентно).

    Workflow-метки `status::*` живут ТОЛЬКО на уровне группы. Если завести их
    ещё и на проектах, снятие метки по имени (add_labels/remove_labels в PUT
    issue) станет неоднозначным и сломает перемещение карточек на доске.
    """
    r = api("GET", f"/groups/{group_id}/labels", params={"per_page": 100})
    for la in (r.json() if r.status_code == 200 else []):
        if la["name"] == name:
            return la
    r = api("POST", f"/groups/{group_id}/labels", json={"name": name, "color": color})
    # 409 = уже существует (гонка/повтор) — добираем из GET
    if r.status_code == 409:
        r2 = api("GET", f"/groups/{group_id}/labels", params={"per_page": 100})
        for la in (r2.json() if r2.status_code == 200 else []):
            if la["name"] == name:
                return la
    return ok(r)


def ensure_project_board(project_id: int, name: str, list_label_ids: list[int]) -> dict:
    """Создать/найти доску ПРОЕКТА «name» со списками по меткам (идемпотентно).

    Проектные доски в CE создаются через REST (POST /projects/:id/boards).
    Группах доски через REST в CE недоступны (EE-only) — см. seed/group_board.rb.
    Списки добавляются только недостающие, существующие не дублируются.
    """
    r = api("GET", f"/projects/{project_id}/boards", params={"per_page": 100})
    board = None
    for b in (r.json() if r.status_code == 200 else []):
        if b["name"] == name:
            board = b
            break
    if board is None:
        board = ok(api("POST", f"/projects/{project_id}/boards", json={"name": name}))
        print(f"  created project board '{name}' (id={board['id']})")
    else:
        print(f"  project board '{name}' exists (id={board['id']})")

    # текущие списки доски → какие label_id уже представлены
    lr = api("GET", f"/projects/{project_id}/boards/{board['id']}/lists")
    existing = {
        (ls.get("label") or {}).get("id")
        for ls in (lr.json() if lr.status_code == 200 else [])
        if ls.get("label")
    }
    for lid in list_label_ids:
        if lid in existing:
            continue
        rr = api("POST", f"/projects/{project_id}/boards/{board['id']}/lists",
                 json={"label_id": lid})
        if rr.status_code < 300:
            print(f"    + list for label_id={lid}")
        elif rr.status_code not in (409, 400):
            ok(rr)
    return board


def ensure_milestone(group_id: int, title: str) -> dict:
    r = api("GET", f"/groups/{group_id}/milestones", params={"search": title})
    for m in (r.json() if r.status_code == 200 else []):
        if m["title"] == title:
            return m
    return ok(api("POST", f"/groups/{group_id}/milestones", json={"title": title}))


def create_issue(project_id: int, title: str, **fields) -> dict:
    # идемпотентность по заголовку
    r = api("GET", f"/projects/{project_id}/issues", params={"search": title, "in": "title"})
    for it in (r.json() if r.status_code == 200 else []):
        if it["title"] == title:
            return it
    return ok(api("POST", f"/projects/{project_id}/issues",
                  json={"title": title, **fields}))


def link_issues(pid: int, iid: int, tgt_project: int, tgt_iid: int, link_type: str) -> str:
    r = api("POST", f"/projects/{pid}/issues/{iid}/links", json={
        "target_project_id": tgt_project, "target_issue_iid": tgt_iid,
        "link_type": link_type})
    if r.status_code < 300:
        return link_type
    # blocks недоступен на Free — деградируем до relates_to
    if link_type != "relates_to":
        r2 = api("POST", f"/projects/{pid}/issues/{iid}/links", json={
            "target_project_id": tgt_project, "target_issue_iid": tgt_iid,
            "link_type": "relates_to"})
        if r2.status_code < 300:
            return "relates_to(fallback)"
    return f"skip({r.status_code})"


# ── модель данных стенда ────────────────────────────────────────────────────
#
# Доски (boards) — две демо-доски «Workflow»:
#   • ПРОЕКТНАЯ доска webapp со списками priority::high/medium/low — создаётся
#     ниже через REST (ensure_project_board); это воспроизводимо в CE.
#   • ГРУППОВАЯ доска со списками status::* — REST в CE НЕДОСТУПЕН (EE-only:
#     POST /groups/:id/boards отдаёт 404 в этом GitLab v19 CE, enterprise:false).
#     Создаётся отдельным rails-скриптом seed/group_board.rb:
#         docker cp seed/group_board.rb ig-gitlab:/tmp/group_board.rb
#         docker exec ig-gitlab gitlab-rails runner /tmp/group_board.rb
#     GET /groups/:id/boards при этом доску ВОЗВРАЩАЕТ (читать можно, создавать
#     нельзя). Скрипт идемпотентен и не дублирует доску/списки.

# Проектные метки (НЕ содержат status::* — те живут только на уровне группы,
# см. GROUP_LABELS). priority::* используются как списки проектной доски.
LABELS = [
    ("frontend", "#1f78d1"), ("backend", "#5cb85c"), ("infra", "#8e44ad"),
    ("bug", "#d9534f"), ("feature", "#0e8a16"), ("tech-debt", "#fbca04"),
    ("priority::high", "#b60205"), ("priority::medium", "#fbca04"),
    ("priority::low", "#c2e0c6"),
]

# Workflow-метки рабочего процесса — ТОЛЬКО на уровне группы (колонки доски).
# Отдельные цвета, чтобы колонки доски визуально различались.
GROUP_LABELS = [
    ("status::todo", "#6699cc"), ("status::doing", "#33aa55"),
    ("status::review", "#cc9933"), ("status::blocked", "#cc3333"),
]
USERS = [("alice", "Alice Dev"), ("bob", "Bob Builder"), ("carol", "Carol Ops")]
MILESTONES = ["Sprint 1", "Sprint 2", "Q3: Платёжный модуль"]

# key: (project_key, title, labels, milestone_idx, assignee_idx, [related keys])
ISSUES = [
    ("api",  "Спроектировать схему БД заказов",      ["backend", "feature"], 0, 0, []),
    ("api",  "Эндпоинт создания заказа",             ["backend", "feature"], 0, 0, ["Спроектировать схему БД заказов"]),
    ("api",  "Эндпоинт статуса заказа",              ["backend"], 0, 1, ["Спроектировать схему БД заказов"]),
    ("api",  "Интеграция с платёжным шлюзом",        ["backend", "feature", "priority::high"], 2, 1, ["Эндпоинт создания заказа"]),
    ("api",  "Вебхуки об оплате",                    ["backend", "priority::high"], 2, 1, ["Интеграция с платёжным шлюзом"]),
    ("api",  "Падает создание заказа при пустой корзине", ["backend", "bug"], 1, 0, ["Эндпоинт создания заказа"]),
    ("api",  "Рейт-лимит на API заказов",            ["backend", "tech-debt"], 1, 2, ["Эндпоинт создания заказа"]),
    ("web",  "Страница оформления заказа",           ["frontend", "feature"], 0, 2, ["Эндпоинт создания заказа"]),
    ("web",  "Компонент корзины",                    ["frontend", "feature"], 0, 2, []),
    ("web",  "Экран статуса оплаты",                 ["frontend"], 2, 2, ["Эндпоинт статуса заказа", "Вебхуки об оплате"]),
    ("web",  "Адаптив для мобильных",                ["frontend", "tech-debt"], 1, 2, ["Страница оформления заказа", "Компонент корзины"]),
    ("web",  "Кнопка оплаты не активна в Safari",    ["frontend", "bug", "priority::high"], 1, 0, ["Страница оформления заказа"]),
    ("infra","Пайплайн CI для api",                  ["infra"], 0, 1, []),
    ("infra","Пайплайн CI для web",                  ["infra"], 0, 1, []),
    ("infra","Стейджинг-окружение",                  ["infra", "feature"], 1, 1, ["Пайплайн CI для api", "Пайплайн CI для web"]),
    ("infra","Мониторинг платёжных вебхуков",        ["infra", "priority::high"], 2, 1, ["Вебхуки об оплате", "Стейджинг-окружение"]),
    ("infra","Бэкапы БД заказов",                    ["infra"], 1, 1, ["Спроектировать схему БД заказов"]),
]
PROJECTS = {"api": ("api", "API"), "web": ("webapp", "Web App"), "infra": ("infra", "Infra")}


def main() -> None:
    g = ensure_group()
    gid = g["id"]

    users = [ensure_user(u, n) for u, n in USERS]
    for u in users:
        ensure_member(gid, u["id"])

    milestones = [ensure_milestone(gid, t) for t in MILESTONES]

    # Workflow-метки status::* — только на уровне ГРУППЫ (колонки доски).
    for ln, col in GROUP_LABELS:
        ensure_group_label(gid, ln, col)
    print(f"  group labels: {', '.join(n for n, _ in GROUP_LABELS)}")

    projects: dict[str, dict] = {}
    for key, (path, name) in PROJECTS.items():
        p = ensure_project(gid, path, name)
        projects[key] = p
        for ln, col in LABELS:
            ensure_label(p["id"], ln, col)

    # Проектная доска «Workflow» (webapp) со списками priority::* — REST в CE.
    web = projects["web"]
    web_labels = ok(api("GET", f"/projects/{web['id']}/labels",
                        params={"per_page": 100}))
    by_name = {la["name"]: la for la in web_labels}
    prio_ids = [by_name[n]["id"] for n in
                ("priority::high", "priority::medium", "priority::low")
                if n in by_name]
    ensure_project_board(web["id"], "Workflow", prio_ids)

    # создаём issue, запоминаем (project_key,title) -> issue
    created: dict[str, dict] = {}
    for proj_key, title, labels, ms_idx, asg_idx, _rel in ISSUES:
        p = projects[proj_key]
        issue = create_issue(
            p["id"], title,
            labels=",".join(labels),
            milestone_id=milestones[ms_idx]["id"],
            assignee_ids=[users[asg_idx]["id"]],
        )
        created[title] = {"issue": issue, "project_id": p["id"]}
        print(f"  issue [{proj_key}] {title} (#{issue['iid']})")

    # связи
    print("links:")
    for proj_key, title, _l, _m, _a, related in ISSUES:
        if not related:
            continue
        src = created[title]
        for rt in related:
            tgt = created[rt]
            res = link_issues(src["project_id"], src["issue"]["iid"],
                              tgt["project_id"], tgt["issue"]["iid"], "blocks")
            print(f"  {title} -> {rt}: {res}")

    print(f"\nDONE. Группа: {URL}/groups/{GROUP}/-/issues")
    print("Проектная доска (REST):  "
          f"{URL}/{GROUP}/{PROJECTS['web'][0]}/-/boards")
    print("Групповая доска (EE-only через REST) — создать rails-скриптом:")
    print("  docker cp seed/group_board.rb ig-gitlab:/tmp/group_board.rb && \\")
    print("  docker exec ig-gitlab gitlab-rails runner /tmp/group_board.rb")


if __name__ == "__main__":
    main()
