"""Core logic: find child Tasks of an issue, convert them to Issues,
label them `task`, and link each back to the original issue (relates to).

NOTE: the GraphQL schema shifts between GitLab versions. Everything that
touches the schema lives in `_collect()` and the mutations in `run()` --
verify these against your instance's /-/graphql-explorer (see README).
"""
import logging
import os
import time

import requests

log = logging.getLogger("expand-tasks")

GL = os.environ["GITLAB_URL"].rstrip("/")
TOK = os.environ["GITLAB_TOKEN"]
LABEL = os.environ.get("TASK_LABEL", "task")

_session = requests.Session()
_session.headers["Authorization"] = f"Bearer {TOK}"
_GQL = f"{GL}/api/graphql"


def gql(query: str, variables: dict | None = None) -> dict:
    resp = _session.post(_GQL, json={"query": query, "variables": variables or {}})
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def member_can(project_id: int, user_id: int, min_level: int) -> bool:
    """True if user has at least `min_level` access (incl. inherited)."""
    r = _session.get(f"{GL}/api/v4/projects/{project_id}/members/all/{user_id}")
    return r.ok and r.json().get("access_level", 0) >= min_level


def post_note(project_id: int, iid: str, body: str) -> None:
    _session.post(
        f"{GL}/api/v4/projects/{project_id}/issues/{iid}/notes",
        data={"body": body},
    )


def _collect(path: str, iid: str):
    """Return (ctx, [child Task nodes]) for issue `iid`.

    In GitLab 17.6 an Issue *is* a work item -- the GraphQL `Issue` type
    has no `workItem` field, so fetch the work item directly via
    project.workItems. Issue type id is resolved client-side (no
    `workItemTypes(name:)` arg in this version).
    """
    data = gql(
        """
        query($p:ID!, $iids:[String!]) {
          project(fullPath:$p) {
            label(title:"%s"){ id }
            workItemTypes { nodes { id name } }
            workItems(iids:$iids) {
              nodes {
                id
                widgets {
                  ... on WorkItemWidgetHierarchy {
                    children {
                      nodes { id iid title workItemType { name } }
                    }
                  }
                }
              }
            }
          }
        }
        """ % LABEL,
        {"p": path, "iids": [iid]},
    )["project"]

    nodes = data["workItems"]["nodes"]
    if not nodes:
        raise RuntimeError(f"work item iid={iid} not found in {path}")
    wi = nodes[0]

    children = []
    for w in wi["widgets"]:
        if isinstance(w, dict) and "children" in w:
            children = w["children"]["nodes"]
            break
    tasks = [c for c in children if c["workItemType"]["name"] == "Task"]

    issue_type = next(
        (t["id"] for t in data["workItemTypes"]["nodes"]
         if t["name"] == "Issue"),
        None,
    )
    if issue_type is None:
        raise RuntimeError("work item type 'Issue' not found")

    if not data["label"]:
        raise RuntimeError(
            f"label '{LABEL}' not found — create it in the group/project "
            f"(Manage -> Labels)"
        )

    ctx = {
        "parent_wi": wi["id"],
        "label_id": data["label"]["id"],
        "issue_type": issue_type,
    }
    return ctx, tasks


def dry_run(path: str, project_id: int, iid: str) -> None:
    try:
        _, tasks = _collect(path, iid)
    except Exception as exc:                       # noqa: BLE001
        log.exception("dry_run failed")
        post_note(project_id, iid, f"⚠️ `/expand-tasks` ошибка: `{exc}`")
        return

    if not tasks:
        post_note(project_id, iid, "ℹ️ Дочерних Task не найдено.")
        return

    listing = "\n".join(f"- #{t['iid']} {t['title']}" for t in tasks)
    post_note(
        project_id, iid,
        f"🔎 Найдено **{len(tasks)}** Task. Они будут сконвертированы в Issue, "
        f"помечены label `{LABEL}` и связаны (relates to) с #{iid}:\n{listing}\n\n"
        f"➡️ Для запуска ответь комментом: `/expand-tasks confirm`",
    )


def run(path: str, project_id: int, iid: str) -> None:
    try:
        ctx, tasks = _collect(path, iid)
    except Exception as exc:                       # noqa: BLE001
        log.exception("run failed at collect")
        post_note(project_id, iid, f"⚠️ `/expand-tasks confirm` ошибка: `{exc}`")
        return

    if not tasks:
        post_note(project_id, iid, "ℹ️ Дочерних Task не найдено.")
        return

    parent_wi = ctx["parent_wi"]
    label_id = ctx["label_id"]
    issue_type = ctx["issue_type"]

    done, failed = [], []
    for t in tasks:
        wid = t["id"]
        try:
            # 1. detach parent (conversion is blocked while parented)
            gql(
                "mutation($i:WorkItemID!){workItemUpdate(input:{id:$i,"
                "hierarchyWidget:{parentId:null}}){errors}}",
                {"i": wid},
            )
            # 2. Task -> Issue
            gql(
                "mutation($i:WorkItemID!,$t:WorkItemsTypeID!){workItemConvert("
                "input:{id:$i,workItemTypeId:$t}){errors}}",
                {"i": wid, "t": issue_type},
            )
            # 3. label
            gql(
                "mutation($i:WorkItemID!,$l:[LabelID!]){workItemUpdate(input:{id:$i,"
                "labelsWidget:{addLabelIds:$l}}){errors}}",
                {"i": wid, "l": [label_id]},
            )
            # 4. relate to the original issue
            gql(
                "mutation($i:WorkItemID!,$o:[WorkItemID!]!){workItemAddLinkedItems("
                "input:{id:$i,workItemsIds:$o,linkType:RELATED}){errors}}",
                {"i": wid, "o": [parent_wi]},
            )
            done.append(f"#{t['iid']} {t['title']}")
        except Exception as exc:                   # noqa: BLE001
            log.exception("conversion failed for %s", t["iid"])
            failed.append(f"#{t['iid']}: `{exc}`")
        time.sleep(0.3)                            # be gentle with rate limits

    msg = f"✅ Сконвертировано: **{len(done)}**\n"
    msg += "\n".join(f"- {x}" for x in done) or "- (нет)"
    if failed:
        msg += f"\n\n⚠️ Ошибки: **{len(failed)}**\n"
        msg += "\n".join(f"- {x}" for x in failed)
    post_note(project_id, iid, msg)
