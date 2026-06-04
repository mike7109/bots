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
from settings import PUSH_MODES, weekday_names
from admin_html import HTML as _HTML
import cron

log = logging.getLogger("gitlab-notify.admin")
COOKIE = "admin_session"


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
        rules = [
            {"event": r.get("event"), "actions": r.get("actions"),
             "template": r.get("template"), "to": r.get("to")}
            for r in engine.config.get("rules", [])
        ]
        return {
            "enabled": g["enabled"],
            "schedule": {
                "anchor_days": sorted(g["anchor_days"]),
                "anchor_days_label": weekday_names(g["anchor_days"]),
                "weekly_day": g["weekly_day"],
                "skip_weekends": g["skip_weekends"],
                "holidays": g["holidays"],
            },
            "users": users,
            "rules": rules,
            "templates": templates,
            "passes": list(cron.PASSES),
            "push_modes": list(PUSH_MODES),
        }

    @router.post("/api/global")
    async def set_global(request: Request, admin_session: str | None = Cookie(default=None)):
        _guard(admin_session)
        patch = await request.json()
        allowed = {"enabled", "anchor_days", "weekly_day", "skip_weekends", "holidays"}
        return settings.update_global({k: v for k, v in patch.items() if k in allowed})

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
