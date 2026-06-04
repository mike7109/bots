"""Web admin panel for the bot, served by the same FastAPI service.

A single password (env ADMIN_PASSWORD) gates everything — the panel can message
the whole team, so it must not be open. Auth is a signed session cookie derived
from the password (no extra secret, no DB). If ADMIN_PASSWORD is unset the panel
is disabled entirely.

Everything the panel changes (kill switch, per-person mute/push, schedule) is
written to the settings store (settings.py) and takes effect on the next
webhook/cron tick. Read-only views (recipients + invite acceptance, rules,
templates) are computed live.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from botkit.config import env
from botkit.gitlab import GitLabClient
from settings import PUSH_MODES, HAS_ANCHOR, weekday_names
from admin_html import HTML as _HTML
import cron

log = logging.getLogger("gitlab-notify.admin")
COOKIE = "admin_session"

# Representative context for the template preview — populated for every template
# variant (issue / due / overdue / digests / triage / stale / metrics) so the
# operator sees roughly what a real message renders to.
SAMPLE_CTX = {
    "kind": "issue", "action": "overdue", "project": "qa/infra", "iid": "42",
    "title": "Пример: продлить TLS-сертификаты", "url": "https://gitlab.local/qa/infra/-/issues/42",
    "state": "opened", "labels": ["infra", "security"],
    "assignees": ["misha", "d.nikulin"], "author": "misha", "due": "2026-06-10",
    "extra": {
        "mode": "full", "date": "2026-06-05", "total": 3, "open_total": 9, "days": 14,
        "window": 7, "throughput": 5, "wip": 2, "age_med": 4, "cycle_p85": 12,
        "sections": [
            {"emoji": "⏰", "title": "Просрочено", "show_who": True, "items": [
                {"iid": 42, "title": "Продлить TLS-сертификаты", "url": "#",
                 "due": "2026-06-01", "assignees": ["misha"]}]},
            {"emoji": "📋", "title": "Без срока", "items": [
                {"iid": 7, "title": "Обновить документацию", "url": "#",
                 "due": None, "assignees": ["d.nikulin"]}]},
        ],
        "changes": {
            "new": [{"iid": 7, "title": "Новая задача на тебе", "url": "#", "due": "2026-06-12", "assignees": ["misha"]}],
            "moved": [{"iid": 3, "title": "Обновить раннеры", "url": "#", "col": "review", "from": "in progress", "assignees": ["misha"]}],
            "overdue": [{"iid": 42, "title": "Продлить сертификаты", "url": "#", "due": "2026-06-01", "assignees": ["misha"]}],
            "today": [], "due": [], "removed": [],
        },
    },
}


def _expected_token() -> str | None:
    pw = env("ADMIN_PASSWORD")
    if not pw:
        return None
    return hmac.new(pw.encode(), b"admin-session-v1", hashlib.sha256).hexdigest()


def _authed(session: str | None) -> bool:
    expected = _expected_token()
    return bool(expected) and bool(session) and hmac.compare_digest(session, expected)


def _invite_status(engine) -> dict:
    """For each configured user: do we have a DM room, and did they accept it?"""
    matrix = engine.matrix
    try:
        direct = matrix._direct_map()
    except Exception as e:                       # noqa: BLE001 — degrade gracefully
        log.warning("m.direct read failed: %s", e)
        direct = {}
    out = {}
    for login, info in (engine.config.get("users") or {}).items():
        mxid = engine.identity.matrix_id(login)
        status = "none"
        rooms = direct.get(mxid) if mxid else None
        if rooms:
            try:
                status = "accepted" if matrix.membership(rooms[0], mxid) == "join" else "pending"
            except Exception:                    # noqa: BLE001
                status = "pending"
        out[login] = {"name": info.get("name", login), "mxid": mxid, "invite": status}
    return out


def create_admin_router(engine) -> APIRouter:
    router = APIRouter(prefix="/admin")
    settings = engine.settings
    group_id = env("GITLAB_GROUP_ID", "3")

    def _gl() -> GitLabClient:
        return GitLabClient(env("GITLAB_URL", required=True), env("GITLAB_TOKEN", required=True))

    def _guard(session):
        if not _authed(session):
            raise HTTPException(status_code=401, detail="auth required")

    @router.get("", response_class=HTMLResponse)
    def page():
        return HTMLResponse(_HTML)

    @router.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        expected = _expected_token()
        if not expected:
            return JSONResponse({"error": "admin disabled (no ADMIN_PASSWORD)"}, status_code=503)
        if not hmac.compare_digest(
            hmac.new((body.get("password") or "").encode(), b"admin-session-v1", hashlib.sha256).hexdigest(),
            expected,
        ):
            return JSONResponse({"error": "неверный пароль"}, status_code=401)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, expected, httponly=True, samesite="lax", max_age=86400 * 7)
        return resp

    @router.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    @router.get("/api/state")
    def state(admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        g = settings.get_global()
        users = _invite_status(engine)
        for login, u in users.items():
            u.update(settings.user_pref(login))
        templates = sorted(p.name for p in engine.templates_dir.glob("*.j2") if not p.name.startswith("_"))
        rules = []
        for r in engine.config.get("rules", []):
            ov = settings.rule_override(r.get("event")) or {}
            rules.append({
                "event": r.get("event"), "actions": r.get("actions"),
                "template": r.get("template"),
                "to": ov.get("to", r.get("to")),       # effective destination
                "default_to": r.get("to"),
                "enabled": ov.get("enabled", True),
            })
        return {
            "enabled": g["enabled"],
            "schedule": {
                "anchor_days": sorted(g["anchor_days"]),
                "anchor_days_label": weekday_names(g["anchor_days"]),
                "weekly_day": g["weekly_day"],
                "skip_weekends": g["skip_weekends"],
                "holidays": g["holidays"],
                "holidays_auto": g.get("holidays_auto", False),
            },
            "pass_schedules": settings.all_pass_schedules(),
            "has_anchor": list(HAS_ANCHOR),
            "users": users,
            "rules": rules,
            "templates": templates,
            "passes": list(cron.PASSES),
            "push_modes": list(PUSH_MODES),
            "stats": engine.store.log_stats(7),
        }

    @router.post("/api/global")
    async def set_global(request: Request, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        patch = await request.json()
        allowed = {"enabled", "anchor_days", "weekly_day", "skip_weekends", "holidays", "holidays_auto"}
        return settings.update_global({k: v for k, v in patch.items() if k in allowed})

    @router.post("/api/pass/{name}")
    async def set_pass(name: str, request: Request, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        if name not in cron.PASSES:
            raise HTTPException(status_code=400, detail="unknown pass")
        body = await request.json()
        clean = {}
        if "enabled" in body:
            clean["enabled"] = bool(body["enabled"])
        if "days" in body and isinstance(body["days"], list):
            clean["days"] = sorted({int(d) for d in body["days"] if 0 <= int(d) <= 6})
        if "anchor_days" in body and isinstance(body["anchor_days"], list):
            clean["anchor_days"] = sorted({int(d) for d in body["anchor_days"] if 0 <= int(d) <= 6})
        if "time" in body and isinstance(body["time"], str) and len(body["time"]) == 5:
            clean["time"] = body["time"]
        return settings.update_pass(name, clean)

    @router.post("/api/user/{login}")
    async def set_user(login: str, request: Request, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        patch = await request.json()
        clean = {}
        if "muted" in patch:
            clean["muted"] = bool(patch["muted"])
        if "push" in patch and patch["push"] in PUSH_MODES:
            clean["push"] = patch["push"]
        return settings.update_user(login, clean)

    @router.post("/api/trigger/{name}")
    def trigger(name: str, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        if name not in cron.PASSES:
            raise HTTPException(status_code=400, detail="unknown pass")
        try:
            sent = cron.run_one(engine, _gl(), group_id, settings.store, name, force=True)
            return {"ok": True, "pass": name, "sent": sent}
        except Exception as e:                   # noqa: BLE001
            log.exception("manual trigger %s failed", name)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @router.post("/api/rule/{event}")
    async def set_rule(event: str, request: Request, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        body = await request.json()
        patch = {}
        if "enabled" in body:
            patch["enabled"] = bool(body["enabled"])
        if "to" in body and isinstance(body["to"], list) and all(t in ("room", "dm") for t in body["to"]):
            patch["to"] = body["to"]
        return settings.update_rule(event, patch)

    @router.get("/api/logs")
    def logs(limit: int = 100, status: str | None = None,
             admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        return {"rows": settings.store.recent_log(min(limit, 500), status),
                "stats": settings.store.log_stats(7)}

    @router.get("/api/example")
    def example(template: str, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        try:
            return {"ok": True, "html": engine.renderer.render(template, "matrix", SAMPLE_CTX)}
        except Exception as e:                       # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @router.post("/api/template/preview")
    async def preview(request: Request, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        body = await request.json()
        try:
            html = engine.renderer.render_string(body.get("content", ""), SAMPLE_CTX)
            return {"ok": True, "html": html}
        except Exception as e:                       # noqa: BLE001 — show the Jinja error
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/api/template")
    def get_template(name: str, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        path = engine.templates_dir / Path(name).name
        if not path.exists():
            raise HTTPException(status_code=404, detail="not found")
        return {"name": path.name, "content": path.read_text(encoding="utf-8")}

    @router.post("/api/template")
    async def save_template(request: Request, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        body = await request.json()
        path = engine.templates_dir / Path(body.get("name", "")).name
        if not path.exists():
            raise HTTPException(status_code=404, detail="not found")
        try:
            path.write_text(body.get("content", ""), encoding="utf-8")
            return {"ok": True}
        except OSError as e:                      # e.g. read-only mount in prod
            return JSONResponse({"ok": False, "error": f"{e} (шаблоны смонтированы read-only?)"},
                                status_code=500)

    return router
