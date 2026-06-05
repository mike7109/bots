"""GitLab webhook -> Matrix notifications.

What gets sent and how it looks is entirely in config.yaml + templates/ —
this file only verifies the webhook, normalizes the payload, and hands the
event to the engine.
"""
import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.staticfiles import StaticFiles

from botkit.config import env
from botkit.webhook import verify_token
from admin import create_admin_router
from normalize import normalize
from scheduler import run_scheduler
from wiring import build_context

logging.basicConfig(
    level=env("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

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

    # Multi-group routing: send to the matching source's room. No source for this
    # project's group -> nowhere configured to send, so ignore it.
    src = ctx.sources.match_path(event.project)
    if not src:
        return {"ignored": f"no source for project {event.project}"}
    # Optional: quiet realtime issue-events outside the active-hours window too
    # (default OFF — webhooks normally stay realtime regardless of the window).
    if ctx.settings.get_active_hours().get("webhooks_quiet") and not ctx.settings.is_active_now():
        return {"ignored": "тихие часы"}
    event.room = src.get("room")
    return ctx.engine.handle(event)
