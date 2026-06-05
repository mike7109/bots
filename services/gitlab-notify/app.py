"""GitLab webhook -> Matrix notifications.

What gets sent and how it looks is entirely in config.yaml + templates/ —
this file only verifies the webhook, normalizes the payload, and hands the
event to the engine.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request

from botkit.config import env
from botkit.webhook import verify_token
from admin import create_admin_router
from normalize import normalize
from scheduler import run_scheduler
from wiring import build_engine

logging.basicConfig(
    level=env("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

engine = build_engine()   # webhook secret is read per-request from settings (DB over env)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Internal scheduler fires digests per their schedule (admin panel). Disable
    # with SCHEDULER_ENABLED=false (e.g. if you'd rather drive cron.py from host).
    task = stop = None
    if env("SCHEDULER_ENABLED", "true").lower() != "false":
        stop = asyncio.Event()
        task = asyncio.create_task(run_scheduler(engine, stop))   # iterates sources itself
    yield
    if task:
        stop.set()
        task.cancel()


app = FastAPI(title="gitlab-notify", lifespan=lifespan)
# Web admin panel at /admin (gated by env ADMIN_PASSWORD; disabled if unset).
app.include_router(create_admin_router(engine))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, x_gitlab_token: str = Header(default="")):
    verify_token(x_gitlab_token, engine.settings.conn_value("webhook_secret"))
    payload = await request.json()

    event = normalize(payload)
    if event is None:
        return {"ignored": f"unhandled event: {payload.get('object_kind')}"}

    # Multi-group routing: send to the matching source's room (else default room).
    src = engine.sources.match_path(event.project)
    if src:
        event.room = src.get("room")
    return engine.handle(event)
