"""Web admin panel for the bot, served by the same FastAPI service.

A single password (env ADMIN_PASSWORD) gates everything — the panel can message
the whole team, so it must not be open. If ADMIN_PASSWORD is unset the panel is
disabled entirely.

Auth model (see auth.py): a successful password check mints a *signed, expiring*
session token, signed with a per-deployment secret persisted in the state DB.
The token carries its own expiry and can be revoked wholesale by rotating that
secret — unlike the old static password-derived cookie. Auth is enforced
correct-by-default: every privileged route lives on a router with a
`require_admin` dependency, so a new route is protected unless it is explicitly
placed on the public router.

Everything the panel changes (kill switch, per-person mute/push, schedule) is
written to the settings store (settings.py) and takes effect on the next
webhook/cron tick. Read-only views (recipients + invite acceptance, rules,
templates) are computed live.
"""
from __future__ import annotations

import hmac
import logging
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from botkit.config import env
from botkit.gitlab import GitLabClient
from botkit.matrix import MatrixClient
from settings import PUSH_MODES, HAS_ANCHOR, weekday_names
from admin_html import HTML as _HTML
import auth
import cron
import keys

log = logging.getLogger("gitlab-notify.admin")
COOKIE = "admin_session"


def _cookie_secure(request) -> bool:
    """Whether to set the Secure flag on the session cookie.

    Default: auto — Secure only when the request arrived over HTTPS. This avoids
    a silent foot-gun: a Secure cookie set over plain HTTP to a *non*-localhost
    host is accepted by the server but then refused by the browser on the next
    request, so the operator looks permanently "logged out". On HTTPS we set
    Secure for real protection; on the documented http://127.0.0.1 deploy the
    cookie works (loopback isn't sniffable anyway). Operators can force it either
    way with ADMIN_COOKIE_SECURE=1/0 — set =1 behind an HTTPS-terminating reverse
    proxy that forwards as http (run uvicorn with --proxy-headers there)."""
    override = env("ADMIN_COOKIE_SECURE")
    if override is not None and override.strip() != "":
        return override.strip().lower() not in ("0", "false", "no")
    return request.url.scheme == "https"


def _mask(token: str | None) -> str:
    return ("…" + token[-4:]) if token else ""

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


def _invite_status(ctx) -> dict:
    """For each configured user: do we have a DM room, and did they accept it?"""
    matrix = ctx.matrix
    try:
        direct = matrix._direct_map()
    except Exception as e:                       # noqa: BLE001 — degrade gracefully
        log.warning("m.direct read failed: %s", e)
        direct = {}
    out = {}
    for login, info in (ctx.config.get("users") or {}).items():
        mxid = ctx.identity.matrix_id(login)
        status = "none"
        rooms = direct.get(mxid) if mxid else None
        if rooms:
            try:
                status = "accepted" if matrix.membership(rooms[0], mxid) == "join" else "pending"
            except Exception:                    # noqa: BLE001
                status = "pending"
        out[login] = {"name": info.get("name", login), "mxid": mxid, "invite": status}
    return out


def create_admin_router(ctx) -> list[APIRouter]:
    """Build the admin panel routers.

    Returns TWO routers, both prefixed "/admin":
      - public: the page, /api/login, /api/logout (no auth — that's how you log in)
      - protected: every other /api/* route, gated by the `require_admin`
        dependency so auth is enforced centrally (correct-by-default — a new
        protected route can't accidentally ship unauthenticated).
    app.py includes both.
    """
    settings = ctx.settings
    store = ctx.store

    # Weak-password nudge (once, at build time). Don't block startup — the panel
    # may legitimately be disabled (no password) on a fresh boot.
    pw = env("ADMIN_PASSWORD")
    if pw and len(pw) < 12:
        log.warning("ADMIN_PASSWORD is short (%d chars) — use a long random value "
                    "(the panel can message the whole team).", len(pw))

    # Per-IP login throttle, shared across requests for the life of the process.
    throttle = auth.LoginThrottle()

    def require_admin(admin_session: str | None = Cookie(default=None)) -> None:
        """FastAPI dependency: 401 unless the cookie holds a valid session token.
        Attached to the protected router so every route on it is authed."""
        if not auth.verify_session(admin_session, auth.get_or_create_secret(store)):
            raise HTTPException(status_code=401, detail="auth required")

    public = APIRouter(prefix="/admin")
    router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    @public.get("", response_class=HTMLResponse)
    def page():
        return HTMLResponse(_HTML)

    @public.post("/api/login")
    async def login(request: Request):
        pw = env("ADMIN_PASSWORD")
        if not pw:
            return JSONResponse({"error": "admin disabled (no ADMIN_PASSWORD)"}, status_code=503)
        # Per-IP lockout: blunt the online brute-force of the single shared
        # password. request.client may be None behind some test transports.
        ip = request.client.host if request.client else "?"
        if throttle.is_locked(ip):
            retry = int(throttle.retry_after(ip)) + 1
            return JSONResponse(
                {"error": f"слишком много попыток, подождите {retry} с"},
                status_code=429, headers={"Retry-After": str(retry)})
        body = await request.json()
        # Constant-time password compare (no timing oracle on the shared secret).
        if not hmac.compare_digest((body.get("password") or "").encode(), pw.encode()):
            throttle.record_failure(ip)
            return JSONResponse({"error": "неверный пароль"}, status_code=401)
        throttle.reset(ip)   # clean slate after a good login
        token = auth.mint_session(auth.get_or_create_secret(store))
        resp = JSONResponse({"ok": True})
        # secure: localhost is a secure context even over http (see _cookie_secure).
        # samesite=strict: the cookie isn't sent on cross-site requests (CSRF guard).
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                        secure=_cookie_secure(request), max_age=auth.SESSION_TTL)
        return resp

    @public.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    @router.get("/api/state")
    def state():
        g = settings.get_global()
        users = _invite_status(ctx)
        for login, u in users.items():
            u.update(settings.user_pref(login))
        templates = sorted(p.name for p in ctx.templates_dir.glob("*.j2") if not p.name.startswith("_"))
        # Last run per enabled source × pass — so the dashboard can show when a
        # scheduled pass last fired (and whether it failed).
        last_runs = {}
        for src in ctx.sources.enabled():
            for pname in cron.PASSES:
                schedkey = keys.ns(src["id"], pname)
                last_runs[schedkey] = store.last_run(schedkey)
        rules = []
        for r in ctx.config.get("rules", []):
            ov = settings.rule_override(r.get("event")) or {}
            rules.append({
                "event": r.get("event"), "actions": r.get("actions"),
                "template": r.get("template"),
                "to": ov.get("to", r.get("to")),                 # effective destination
                "default_to": r.get("to"),
                "enabled": ov.get("enabled", r.get("enabled", True)),  # config default, admin overrides
            })
        return {
            "enabled": g["enabled"],
            "scheduler_on": g.get("scheduler_on", True),
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
            "stats": ctx.store.log_stats(7),
            "alerts": settings.get_alerts(),
            "last_runs": last_runs,
            "conn": (lambda c: {
                "matrix_homeserver": c["matrix_homeserver"],
                "matrix_token_masked": _mask(c["matrix_token"]), "has_matrix_token": bool(c["matrix_token"]),
                "webhook_secret_masked": _mask(c["webhook_secret"]), "has_webhook_secret": bool(c["webhook_secret"]),
                "gitlab_url": c["gitlab_url"],
            })(settings.get_conn()),
            "gitlab_url": settings.conn_value("gitlab_url"),
            "sources": [
                {"id": s["id"], "name": s.get("name"), "group_id": s.get("group_id"),
                 "room": s.get("room"), "enabled": s.get("enabled", True),
                 "full_path": s.get("full_path"), "group_name": s.get("group_name"),
                 "token_masked": _mask(s.get("token")), "has_token": bool(s.get("token"))}
                for s in ctx.sources.all()
            ],
        }

    @router.post("/api/global")
    async def set_global(request: Request):
        patch = await request.json()
        allowed = {"enabled", "scheduler_on", "anchor_days", "weekly_day",
                   "skip_weekends", "holidays", "holidays_auto"}
        return settings.update_global({k: v for k, v in patch.items() if k in allowed})

    @router.post("/api/alerts")
    async def set_alerts(request: Request):
        body = await request.json()
        patch = {}
        if "enabled" in body:
            patch["enabled"] = bool(body["enabled"])
        if isinstance(body.get("engineers"), list):
            known = set(ctx.config.get("users", {}) or {})
            # Silently drop unknown logins — only configured users can be alerted.
            patch["engineers"] = [str(e) for e in body["engineers"] if str(e) in known]
        return settings.update_alerts(patch)

    @router.post("/api/pass/{name}")
    async def set_pass(name: str, request: Request):
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
        if "days_idle" in body:                          # stale: N days of inactivity
            try:
                clean["days_idle"] = max(1, int(body["days_idle"]))
            except (ValueError, TypeError):
                pass
        return settings.update_pass(name, clean)

    @router.post("/api/user/{login}")
    async def set_user(login: str, request: Request):
        patch = await request.json()
        clean = {}
        if "muted" in patch:
            clean["muted"] = bool(patch["muted"])
        if "push" in patch and patch["push"] in PUSH_MODES:
            clean["push"] = patch["push"]
        return settings.update_user(login, clean)

    @router.post("/api/trigger/{name}")
    def trigger(name: str):
        if name not in cron.PASSES:
            raise HTTPException(status_code=400, detail="unknown pass")
        url = settings.conn_value("gitlab_url")
        total, per = 0, []
        for src in ctx.sources.enabled():
            if not src.get("group_id"):
                continue
            try:
                gl = GitLabClient(url, src.get("token", ""))
                n = cron.run_one(ctx.engine, gl, src["group_id"], ctx.store, name,
                                 force=True, room=src.get("room"), skey=src["id"])
                total += n
                per.append({"source": src["id"], "sent": n})
            except Exception as e:               # noqa: BLE001 — one source mustn't fail the rest
                log.exception("manual trigger %s for %s failed", name, src["id"])
                per.append({"source": src["id"], "error": str(e)})
        return {"ok": True, "pass": name, "sent": total, "per": per}

    @router.post("/api/rule/{event}")
    async def set_rule(event: str, request: Request):
        body = await request.json()
        patch = {}
        if "enabled" in body:
            patch["enabled"] = bool(body["enabled"])
        if "to" in body and isinstance(body["to"], list) and all(t in ("room", "dm") for t in body["to"]):
            patch["to"] = body["to"]
        return settings.update_rule(event, patch)

    @router.get("/api/logs")
    def logs(limit: int = 100, status: str | None = None):
        return {"rows": store.recent_log(min(limit, 500), status),
                "stats": store.log_stats(7)}

    @router.get("/api/example")
    def example(template: str):
        try:
            return {"ok": True, "html": ctx.renderer.render(template, "matrix", SAMPLE_CTX)}
        except Exception as e:                       # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @router.post("/api/template/preview")
    async def preview(request: Request):
        body = await request.json()
        try:
            html = ctx.renderer.render_string(body.get("content", ""), SAMPLE_CTX)
            return {"ok": True, "html": html}
        except Exception as e:                       # noqa: BLE001 — show the Jinja error
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @router.post("/api/invite-blast")
    def invite_blast():
        """Post one message to the shared room @-tagging everyone who hasn't
        accepted the bot's DM invite, asking them to connect — and make sure a
        standing DM invite exists for each."""
        room = (ctx.config.get("defaults") or {}).get("room_id")
        if not room:
            return JSONResponse({"ok": False, "error": "не задана общая комната (MATRIX_ROOM)"},
                                status_code=400)
        targets = []
        for login, info in _invite_status(ctx).items():
            if info["invite"] != "accepted" and info["mxid"]:
                try:
                    ctx.matrix.get_or_create_dm(info["mxid"])   # ensure invite exists
                except Exception:                                  # noqa: BLE001
                    log.warning("could not create DM for %s", login)
                targets.append((login, info["mxid"]))
        if not targets:
            return {"ok": True, "pinged": [], "note": "все уже приняли бота"}
        pills = " ".join(str(ctx.identity.matrix_pill(login)) for login, _ in targets)
        html = (f"🔔 <b>Подключите бота</b><br>{pills} — примите приглашение бота в личку, "
                "чтобы получать персональные напоминания и дайджесты приватно. "
                "Откройте DM-инвайт от бота и нажмите «Принять».")
        ctx.matrix.send_html(room, html, mention_user_ids=[m for _, m in targets], notice=False)
        return {"ok": True, "pinged": [login for login, _ in targets]}

    @router.get("/api/template")
    def get_template(name: str):
        path = ctx.templates_dir / Path(name).name
        if not path.exists():
            raise HTTPException(status_code=404, detail="not found")
        override = store.get_state("template", path.name)   # admin edit (DB), if any
        return {
            "name": path.name,
            "content": override if override is not None else path.read_text(encoding="utf-8"),
            "default": path.read_text(encoding="utf-8"),
            "overridden": override is not None,
        }

    @router.post("/api/template")
    async def save_template(request: Request):
        body = await request.json()
        path = ctx.templates_dir / Path(body.get("name", "")).name
        if not path.exists():                     # only edit known templates
            raise HTTPException(status_code=404, detail="not found")
        content = body.get("content", "")
        try:
            ctx.renderer.env.from_string(content)   # reject broken Jinja before it ships
        except Exception as e:                          # noqa: BLE001
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=400)
        # Save as a DB override (templates on disk stay read-only / immutable).
        store.set_state("template", path.name, content)
        return {"ok": True}

    @router.post("/api/template/reset")
    async def reset_template(request: Request):
        body = await request.json()
        name = Path(body.get("name", "")).name
        store.clear_state("template", name)   # back to the on-disk default
        return {"ok": True}

    # --- multi-group sources -------------------------------------------------
    @router.post("/api/sources/validate")
    async def validate_source(request: Request):
        body = await request.json()
        token = body.get("token") or ""
        if token.startswith("…") and body.get("id"):     # masked -> reuse stored token
            existing = ctx.sources.get(body["id"])
            token = (existing or {}).get("token", "")
        return ctx.sources.validate(str(body.get("group_id", "")), token)

    @router.post("/api/sources")
    async def save_source(request: Request):
        body = await request.json()
        gid = str(body.get("group_id", "")).strip()
        if not gid or not (body.get("room") or "").strip():
            return JSONResponse({"ok": False, "error": "нужны group_id и room"}, status_code=400)
        sid = (body.get("id") or "").strip() or f"g{gid}"
        token = body.get("token") or ""
        if not token or token.startswith("…"):           # blank/masked -> keep stored token
            token = (ctx.sources.get(sid) or {}).get("token", "")
        src = {"id": sid, "name": body.get("name") or sid, "group_id": gid,
               "token": token, "room": body["room"].strip(),
               "enabled": bool(body.get("enabled", True))}
        # Auto-fill full_path/group_name so webhook routing works; best-effort.
        if token:
            v = ctx.sources.validate(gid, token)
            if v.get("ok"):
                src["full_path"] = v.get("full_path")
                src["group_name"] = v.get("group_name")
        return {"ok": True, "source": {**ctx.sources.upsert(src), "token": None}}

    @router.delete("/api/sources/{sid}")
    def delete_source(sid: str):
        ctx.sources.delete(sid)
        return {"ok": True}

    # --- connection settings (Matrix / webhook / GitLab) ---------------------
    @router.post("/api/conn")
    async def set_conn(request: Request):
        body = await request.json()
        patch = {}
        for f in ("matrix_homeserver", "matrix_token", "webhook_secret", "gitlab_url"):
            if f in body and not (isinstance(body[f], str) and body[f].startswith("…")):
                patch[f] = body[f]                  # skip masked (unchanged) secrets
        settings.update_conn(patch)
        if "matrix_homeserver" in patch or "matrix_token" in patch:   # apply to live client
            ctx.matrix.reconfigure(settings.conn_value("matrix_homeserver"),
                                   settings.conn_value("matrix_token"))
        return {"ok": True}

    @router.post("/api/conn/check-matrix")
    async def check_matrix(request: Request):
        body = await request.json()
        hs = body.get("homeserver") or settings.conn_value("matrix_homeserver")
        tok = body.get("token") or ""
        if not tok or tok.startswith("…"):
            tok = settings.conn_value("matrix_token")
        if not hs or not tok:
            return {"ok": False, "error": "не задан homeserver или токен"}
        try:
            return {"ok": True, "user_id": MatrixClient(hs, tok).user_id}
        except Exception as e:                       # noqa: BLE001
            return {"ok": False, "error": str(e)}

    @router.get("/api/health")
    def health():
        checks = []

        def add(name, ok, detail=""):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        conn = settings.get_conn()
        bot_mxid = None
        try:
            bot_mxid = ctx.matrix.user_id
            add("Matrix-подключение", True, f"бот {bot_mxid}")
        except Exception as e:                       # noqa: BLE001
            add("Matrix-подключение", False, f"токен/хост не работают: {e}")
        add("Секрет вебхука", bool(conn["webhook_secret"]),
            "задан" if conn["webhook_secret"] else "не задан — вебхуки отклоняются")
        add("GitLab URL", bool(conn["gitlab_url"]), conn["gitlab_url"] or "не задан")

        srcs = ctx.sources.enabled()
        add("Группы (источники)", len(srcs) > 0,
            f"включено: {len(srcs)}" if srcs else "ни одной — добавь во вкладке «Группы»")
        for s in srcs:
            label = s.get("name") or s["id"]
            v = ctx.sources.validate(str(s.get("group_id", "")), s.get("token", ""))
            add(f"«{label}»: доступ к GitLab", v.get("ok"),
                f"{v.get('group_name')} · issue {v.get('issues')}" if v.get("ok") else v.get("error", "нет доступа"))
            if s.get("room"):
                if bot_mxid:
                    try:
                        m = ctx.matrix.membership(s["room"], bot_mxid)
                        add(f"«{label}»: бот в комнате", m == "join",
                            "в комнате" if m == "join" else f"не вступил (membership={m}) — пригласи бота")
                    except Exception as e:           # noqa: BLE001
                        add(f"«{label}»: бот в комнате", False, str(e))
                else:
                    add(f"«{label}»: бот в комнате", False, "сначала почини Matrix-подключение")
            else:
                add(f"«{label}»: комната", False, "не задана")

        return {"ok": all(c["ok"] for c in checks), "checks": checks,
                "configured": all(c["ok"] for c in checks)}

    return [public, router]
