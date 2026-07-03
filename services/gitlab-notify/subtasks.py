"""Child-Task (внутренняя карточка) status changes -> the watched issue's thread.

GitLab 17.6 emits NO webhooks for Task work items — they are reachable only via
GraphQL (established in normalize.py and services/gitlab-expand-tasks). So this
is a POLL, not a webhook: the scheduler loop calls `maybe_poll` every tick, and
at most once per POLL_MINUTES we fetch the children of every issue that has a
Matrix topic (store kind "issue_thread") — one batched GraphQL query per
project — and diff them against the last snapshot (store kind "subtasks", key
"project#iid").

What posts into the thread (per room, nested under the issue's root):
  * a NEW child Task            -> "создана подзадача"
  * open -> closed              -> "закрыта подзадача"
  * closed -> open              -> "переоткрыта подзадача"
Deliberately silent: task comments, assignees, renames, deletions — the thread
carries subtask MILESTONES, not its life story. No label is required on the
task itself: a watched parent makes its children watched (the thread belongs to
the parent, and people would forget to tag tasks).

Anti-spam mirrors the rest of the pipeline:
  * first sight of an issue seeds the baseline SILENTLY (a fresh deploy or a
    new thread never replays existing subtasks);
  * per-room shedding via settings.note_send_allowed BEFORE engine.handle (a
    bulk task import can't trip the global breaker);
  * issues whose parent got CLOSED are dropped from the poll scope (snapshot
    flag) until a reopen webhook calls `parent_reopened` — the scope can't grow
    forever.
Every store/API fault is best-effort: the poll must never kill the scheduler.
"""
from __future__ import annotations

import datetime as dt
import logging

from botkit.gitlab import GitLabClient
from botkit.notify.event import Event

log = logging.getLogger("gitlab-notify.subtasks")

KIND = "subtasks"        # store state kind: key "project#iid" -> {"tasks": {...}}
POLL_MINUTES = 3         # cadence ceiling; in-memory throttle (restart polls sooner)
CHUNK = 50               # iids per GraphQL request

# In GitLab 17.6 an Issue IS a work item (project.workItems, no Issue.workItem
# field) and children ride the hierarchy widget — same shape expand.py uses.
_QUERY = """
query($p: ID!, $iids: [String!]) {
  project(fullPath: $p) {
    workItems(iids: $iids) {
      nodes {
        iid
        state
        widgets {
          ... on WorkItemWidgetHierarchy {
            children { nodes { iid title state workItemType { name } } }
          }
        }
      }
    }
  }
}"""

_last_poll: dt.datetime | None = None


def _reset_poll_state() -> None:
    """Drop the in-memory poll throttle (tests want a clean slate)."""
    global _last_poll
    _last_poll = None


def _state(raw) -> str:
    return "closed" if str(raw or "").upper() == "CLOSED" else "open"


def _fetch(gl: GitLabClient, project: str, iids: list[str]) -> dict:
    """{iid: {"state": open|closed, "tasks": {tiid: {"title", "state"}}}} for the
    requested issues, chunked. Children that aren't Tasks are ignored."""
    out: dict = {}
    for i in range(0, len(iids), CHUNK):
        data = gl.graphql(_QUERY, {"p": project, "iids": [str(x) for x in iids[i:i + CHUNK]]})
        nodes = (((data or {}).get("project") or {}).get("workItems") or {}).get("nodes") or []
        for node in nodes:
            tasks = {}
            for w in node.get("widgets") or []:
                if not (isinstance(w, dict) and "children" in w):
                    continue
                for c in (w.get("children") or {}).get("nodes") or []:
                    if (c.get("workItemType") or {}).get("name") != "Task":
                        continue
                    tasks[str(c.get("iid"))] = {"title": c.get("title") or "",
                                                "state": _state(c.get("state"))}
            out[str(node.get("iid"))] = {"state": _state(node.get("state")), "tasks": tasks}
    return out


def _thread_index(store) -> dict[str, list[str]]:
    """{"project#iid": [room, ...]} from the issue_thread keys app.py writes
    ("<project>#<iid>@<room>"). Malformed keys are skipped."""
    out: dict[str, list[str]] = {}
    try:
        entries = store.list_state("issue_thread")
    except Exception:                               # noqa: BLE001 — poll is best-effort
        log.exception("subtask poll: thread index failed")
        return {}
    for key in entries:
        base, sep, room = key.rpartition("@")
        if not sep or not room or "#" not in base:
            continue
        out.setdefault(base, []).append(room)
    return out


def _rule_enabled(ctx) -> bool:
    """Effective on/off of the `subtask` config rule (admin override wins) —
    checked BEFORE polling so a disabled rule costs zero API traffic."""
    rule = next((r for r in (ctx.config.get("rules") or [])
                 if r.get("event") == "subtask"), None)
    enabled = bool(rule.get("enabled", True)) if rule else False
    ov = ctx.settings.rule_override("subtask") or {}
    if "enabled" in ov:
        enabled = bool(ov["enabled"])
    return enabled


def parent_reopened(store, key: str) -> None:
    """Webhook says the issue reopened -> resume polling its subtasks (poll()
    skips issues whose snapshot marked the parent closed). Tasks added while it
    was closed will diff as "создана" on the next poll — intended."""
    try:
        snap = store.get_state(KIND, key)
        if snap and snap.get("parent_closed"):
            snap.pop("parent_closed", None)
            store.set_state(KIND, key, snap)
    except Exception:                               # noqa: BLE001 — bookkeeping is best-effort
        log.exception("subtask reopen-reset failed for %s", key)


def _post_changes(ctx, project: str, iid: str, rooms: list[str],
                  prev: dict, cur: dict, gitlab_url: str) -> int:
    """Diff one issue's task snapshots and post each transition into the issue's
    thread in every room. Returns the number of delivered messages."""
    events = []
    for tiid in sorted(cur, key=lambda x: (len(x), x)):    # numeric-ish order, stable
        t = cur[tiid]
        p = prev.get(tiid)
        if p is None:
            events.append(("open", tiid, t))
        elif p.get("state") != t["state"]:
            events.append(("close" if t["state"] == "closed" else "reopen", tiid, t))
    # tasks that DISAPPEARED (deleted / converted / re-parented) stay silent.
    sent = 0
    for action, tiid, t in events:
        for rm in rooms:
            try:
                st = ctx.store.get_state("issue_thread", f"{project}#{iid}@{rm}") or {}
            except Exception:                       # noqa: BLE001 — threading is best-effort
                log.exception("subtask thread lookup failed")
                st = {}
            root = st.get("event_id")
            if not root:
                continue                            # no root -> nothing to nest under
            # Shed BEFORE handle (no guard.record), like watched-issue comments: a
            # bulk task import stays readable in GitLab instead of tripping the breaker.
            if not ctx.settings.note_send_allowed(rm):
                continue
            ev = Event(kind="subtask", action=action, project=project, iid=str(tiid),
                       title=t.get("title", ""),
                       url=f"{gitlab_url}/{project}/-/work_items/{tiid}",
                       room=rm, extra={"watched": True, "thread_root": root})
            res = ctx.engine.handle(ev)
            if isinstance(res, dict) and res.get("sent"):
                sent += 1
    return sent


def poll(ctx) -> dict:
    """One full poll: fetch children of every thread'ed issue, diff, post.
    Returns {"issues": examined, "seeded": baselines created, "posted": sent}."""
    store = ctx.store
    threads = _thread_index(store)
    gitlab_url = ctx.settings.conn_value("gitlab_url")
    posted = seeded = examined = 0

    by_project: dict[str, list[str]] = {}
    snaps: dict[str, dict | None] = {}
    for key in threads:
        try:
            snap = store.get_state(KIND, key)
        except Exception:                           # noqa: BLE001 — poll is best-effort
            log.exception("subtask snapshot read failed")
            continue
        if snap and snap.get("parent_closed"):
            continue                                # out of scope until a reopen webhook
        snaps[key] = snap
        project, _, iid = key.rpartition("#")
        by_project.setdefault(project, []).append(iid)

    for project, iids in by_project.items():
        src = ctx.sources.match_path(project) or {}
        token = src.get("token")
        if not (token and gitlab_url):
            log.warning("subtask poll: no source token/url for %s — skipped", project)
            continue
        try:
            fetched = _fetch(GitLabClient(gitlab_url, token, timeout=10.0), project, iids)
        except Exception:                           # noqa: BLE001 — one project mustn't sink the rest
            log.exception("subtask poll: fetch failed for %s", project)
            continue
        for iid in iids:
            cur = fetched.get(str(iid))
            if cur is None:
                continue                            # issue gone/moved — keep the old snapshot
            examined += 1
            key = f"{project}#{iid}"
            prev = snaps.get(key)
            if not prev or not isinstance(prev.get("tasks"), dict):
                seeded += 1                         # first sight: baseline only, no replay
            else:
                try:
                    posted += _post_changes(ctx, project, iid, threads[key],
                                            prev["tasks"], cur["tasks"], gitlab_url)
                except Exception:                   # noqa: BLE001 — one issue mustn't sink the rest
                    log.exception("subtask diff/post failed for %s", key)
            new_snap: dict = {"tasks": cur["tasks"]}
            if cur["state"] == "closed":
                new_snap["parent_closed"] = True    # drop from scope; reopen webhook restores
            try:
                store.set_state(KIND, key, new_snap)
            except Exception:                       # noqa: BLE001 — bookkeeping is best-effort
                log.exception("subtask snapshot store failed for %s", key)
    return {"issues": examined, "seeded": seeded, "posted": posted}


def maybe_poll(ctx, now: dt.datetime | None = None) -> dict | None:
    """The scheduler-loop entry point: throttled to POLL_MINUTES, gated the same
    way webhooks are (kill switch, optional quiet hours, breaker, rule toggle).
    Runs OUTSIDE tick()'s calendar gates on purpose — this poll SUBSTITUTES for
    webhooks (which stay realtime on weekends), not for digests."""
    global _last_poll
    now = now or dt.datetime.now()
    if _last_poll is not None and (now - _last_poll) < dt.timedelta(minutes=POLL_MINUTES):
        return None
    _last_poll = now                                 # mark evaluated even if gated below
    s = ctx.settings
    if not s.enabled():
        return None
    if not _rule_enabled(ctx):
        return None
    if s.get_active_hours().get("webhooks_quiet") and not s.is_active_now(now):
        return None                                  # mirror the webhook quiet-hours option
    if ctx.guard is not None and ctx.guard.blocked():
        return None
    result = poll(ctx)
    if result.get("posted") or result.get("seeded"):
        log.info("subtask poll: %s", result)
    return result
