"""GitLab webhook -> Matrix notifications.

What gets sent and how it looks is entirely in config.yaml + templates/ —
this file only verifies the webhook, normalizes the payload, and hands the
event to the engine.
"""
import asyncio
import dataclasses
import hmac
import logging
import pathlib
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from markupsafe import escape

from botkit.config import env
from botkit.notify.render import _safe_url
from botkit.webhook import verify_token
from admin import create_admin_router
from normalize import normalize
from scheduler import run_scheduler
from wiring import build_context

logging.basicConfig(
    level=env("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("gitlab-notify")

ctx = build_context()   # webhook secret is read per-request from settings (DB over env)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Internal scheduler always runs; whether it actually fires is the admin
    # "Авторассылки" toggle (settings.scheduler_on) + the global kill switch.
    stop = asyncio.Event()
    task = asyncio.create_task(run_scheduler(ctx, stop))   # iterates sources itself
    yield
    stop.set()
    task.cancel()


app = FastAPI(title="gitlab-notify", lifespan=lifespan)
# Web admin panel at /admin (gated by env ADMIN_PASSWORD; disabled if unset).
# create_admin_router returns [public, protected]; the protected router carries a
# require_admin dependency so every privileged route is authed by default.
for _admin_router in create_admin_router(ctx):
    app.include_router(_admin_router)

# Admin CSS/JS as static files (was inline in admin_html.py — extracted so they
# get editor highlighting + a JS syntax check in CI). PUBLIC by design: the login
# page needs the stylesheet/script before auth, and neither asset is sensitive.
# Different path from the /admin page + /admin/api/* routes, so no shadowing.
app.mount("/admin/static",
          StaticFiles(directory=str(pathlib.Path(__file__).parent / "static")),
          name="admin-static")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, x_gitlab_token: str = Header(default="")):
    verify_token(x_gitlab_token, ctx.settings.conn_value("webhook_secret"))
    payload = await request.json()

    event = normalize(payload)
    if event is None:
        return {"ignored": f"unhandled event: {payload.get('object_kind')}"}

    # Watched-issue routing: any enabled rule whose tags intersect this issue's
    # FULL label set adds its rooms as extra targets — independent of the source
    # match, so a "special" issue reaches its group even from an unconfigured
    # project. Detection uses event.labels (unfiltered); the display whitelist
    # only trims chips at render time, so it can't hide a watched tag here.
    watch_rooms = []
    for r in ctx.settings.watched_targets(event.labels):
        for rm in r["rooms"]:
            if rm not in watch_rooms:
                watch_rooms.append(rm)

    # Multi-group routing: the matching source's room. `update` (issue edits) and
    # `note` (new comments) are too noisy / watch-scoped for the normal channel, so
    # they go ONLY to watch rooms; open/close/reopen also post to the source room
    # (as before).
    src = ctx.sources.match_path(event.project)
    default_room = src.get("room") if src else None
    watch_only = event.kind == "note" or event.action == "update"
    post_default = not watch_only and bool(default_room)

    if not post_default and not watch_rooms:
        reason = "no watched match" if watch_only else "no source for project"
        return {"ignored": f"{reason} {event.project}".strip()}

    # Optional: quiet realtime issue-events outside the active-hours window too
    # (default OFF — webhooks normally stay realtime regardless of the window).
    if ctx.settings.get_active_hours().get("webhooks_quiet") and not ctx.settings.is_active_now():
        return {"ignored": "тихие часы"}

    # Post to the default room, then each distinct watch room (skipping the
    # default if a watch rule names the same room). `extra.watched` marks the
    # watch copies so the template can flag them (⭐).
    results = {}
    if post_default:
        event.room = default_room
        event.extra = {}
        results["default"] = ctx.engine.handle(event)
    for rm in watch_rooms:
        if post_default and rm == default_room:
            continue
        # SAFETY: comments arrive one webhook each; a chatty watched issue could
        # otherwise push the room past the guard's per-target cap and trip the
        # GLOBAL breaker (halting ALL sending). Shed excess BEFORE handle (no
        # guard.record) — the dropped comments stay readable in GitLab.
        if event.kind == "note" and not ctx.settings.note_send_allowed(rm):
            results[f"throttled:{rm}"] = {"ignored": "throttled"}
            continue
        event.room = rm
        event.extra = {"watched": True}
        # Thread comments of ONE issue together (per room). So even the FIRST
        # comment already sits in a thread, post a small top-level "header"
        # (note_root) as the thread root, then nest every comment under it. Keyed
        # by iid (stable across a rename) — renaming never breaks the thread; a
        # real iid is required so a malformed (iid-less) payload can't collapse
        # distinct issues into one thread. Store faults NEVER break delivery
        # (mirrors engine.py, where store bookkeeping can't sink a send).
        tkey = f"{event.project}#{event.iid}@{rm}" if (event.kind == "note" and event.iid) else None
        root_eid = None
        if tkey:
            try:
                root = ctx.store.get_state("note_thread", tkey)
                if root and root.get("event_id"):
                    root_eid = root["event_id"]
            except Exception:                       # noqa: BLE001 — threading is best-effort
                log.exception("note-thread lookup failed")
            if not root_eid:                        # first comment: post the anchor header
                header = dataclasses.replace(event, kind="note_root", room=rm, extra={})
                hres = ctx.engine.handle(header)
                root_eid = hres.get("event_id") if isinstance(hres, dict) else None
                if root_eid:
                    try:
                        ctx.store.set_state("note_thread", tkey, {"event_id": root_eid})
                    except Exception:               # noqa: BLE001 — threading is best-effort
                        log.exception("note-thread store failed")
            if root_eid:
                event.extra["thread_root"] = root_eid
        res = ctx.engine.handle(event)
        results[f"watch:{rm}"] = res
        # Fallback: the anchor couldn't post (kill switch / breaker) — let the
        # comment itself become the root so a later comment can still thread.
        if tkey and not root_eid and isinstance(res, dict) and res.get("event_id"):
            try:
                ctx.store.set_state("note_thread", tkey, {"event_id": res["event_id"]})
            except Exception:                       # noqa: BLE001 — threading is best-effort
                log.exception("note-thread store failed")
    return {"posted": list(results.keys()), "results": results}


def _poke_fail(reason: str, status: int, **extra) -> JSONResponse:
    return JSONResponse({"ok": False, "reason": reason, **extra}, status_code=status)


@app.post("/api/poke")
async def poke(request: Request, authorization: str = Header(default="")):
    """Public endpoint: issue-graph asks the bot to DM-remind ("пнуть") a GitLab
    assignee about an issue. Fail-closed on the token; soft anti-spam BEFORE the
    send (no guard.record so spam never trips the global breaker); the DM bypasses
    mute and NEVER falls back to a shared room (a poke must reach the person only).

    Contract (from issue-graph): POST /api/poke, header
    `Authorization: Bearer <poke_token>`, JSON body
    {gitlab_username, issue:{iid,title,web_url,project_path}, poked_by?, message?}.
    `message` is an OPTIONAL comment appended below the standard poke (not a
    replacement); `poked_by` is the actor's display name.
    """
    # 1) Token (fail-closed, exactly like webhook_secret): no token configured ->
    #    the feature is OFF; a configured token must match constant-time.
    token = ctx.settings.conn_value("poke_token")
    if not token:
        return _poke_fail("пинок выключен — не задан токен (POKE_TOKEN)", 401)
    bearer = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not bearer or not hmac.compare_digest(bearer, token):
        return _poke_fail("неверный токен", 401)

    # 2) Body.
    try:
        body = await request.json()
    except Exception:                            # noqa: BLE001 — malformed JSON
        body = {}
    username = (body or {}).get("gitlab_username")
    issue = (body or {}).get("issue")
    if not username or not isinstance(issue, dict):
        return _poke_fail("нужны gitlab_username и issue", 400)

    # 3) Global kill switch.
    if not ctx.settings.enabled():
        return _poke_fail("бот выключен", 503)

    # 4) Resolve recipient. The spec example uses reason="user_not_mapped"
    #    literally; we keep that machine token and add a human `detail`.
    mxid = ctx.identity.matrix_id(username)
    if not mxid:
        return _poke_fail("user_not_mapped", 422,
                          detail=f"пользователь «{username}» не сопоставлен с Matrix")

    store = ctx.store
    cfg = ctx.settings.get_poke()
    now = time.time()
    iid = issue.get("iid")

    # 5) Anti-spam — soft 429, NO send, NO guard.record (so a poke flood is
    #    throttled here and can never trip the global breaker).
    prev = store.get_state("poke", f"{username}:{iid}")
    if prev and (now - float(prev.get("ts", 0))) < cfg["cooldown_s"]:
        return _poke_fail("этого исполнителя уже пнули по этой задаче недавно — подождите", 429)
    rate = store.get_state("poke_rate", username) or {"ts": []}
    recent = [t for t in rate.get("ts", []) if (now - float(t)) < cfg["per_user_window_s"]]
    if len(recent) >= cfg["per_user_max"]:
        return _poke_fail("слишком часто пингуете этого человека — подождите", 429)

    # 6) Breaker backstop: if it's already tripped, don't add to the flood.
    if ctx.guard is not None and ctx.guard.blocked():
        return _poke_fail("предохранитель сработал — отправка остановлена", 503)

    # 7) Build the message: the standard poke (who + task + link) ALWAYS, plus an
    #    OPTIONAL free-text comment the poker added in issue-graph, appended below
    #    — not instead of the task context. Title/url come from GitLab and the
    #    comment from a user, so ESCAPE everything.
    title = escape(issue.get("title") or "")
    web_url = issue.get("web_url") or ""
    # Optional «who poked» — issue-graph may send the actor's name/login.
    by = body.get("poked_by")
    by = by.strip() if isinstance(by, str) else ""
    who = f"<b>{escape(by)}</b> пнул тебя" if by else "Тебя пнули"
    html = (f"🔔 {who} по задаче <b>#{escape(iid)} {title}</b><br>"
            f'<a href="{escape(_safe_url(web_url))}">{escape(web_url)}</a>')
    comment = (body.get("message") or "").strip() if isinstance(body.get("message"), str) else ""
    if comment:
        html += "<br>💬 " + str(escape(comment)).replace("\n", "<br>")   # keep line breaks

    # 8) Send the DM directly (bypass mute, no room fallback).
    try:
        dm = ctx.matrix.get_or_create_dm(mxid)
        ctx.matrix.send_html(dm, html, mention_user_ids=[mxid], notice=False)
    except Exception as e:                       # noqa: BLE001 — report the delivery error
        store.log_event("poke", "poke", "dm", "error", f"{username}: {e}"[:200])
        return _poke_fail(f"ошибка доставки: {e}", 502)

    # 9) Success: count toward the breaker (a genuine flood across people still
    #    trips it), record the cooldown + per-user rate, log, and return.
    if ctx.guard is not None:
        ctx.guard.record(f"dm:{mxid}", html)
    store.set_state("poke", f"{username}:{iid}", {"ts": now})
    store.set_state("poke_rate", username, {"ts": [*recent, now]})
    store.log_event("poke", "poke", "dm", "sent", f"{username} <- #{iid} {issue.get('title') or ''}"[:200])
    return JSONResponse({"ok": True, "delivered": True})
