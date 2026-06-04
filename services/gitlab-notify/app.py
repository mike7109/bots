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
from botkit.gitlab import GitLabClient
from botkit.webhook import verify_token
from admin import create_admin_router
from normalize import normalize
from scheduler import run_scheduler
from wiring import build_engine

logging.basicConfig(
    level=env("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

SECRET = env("WEBHOOK_SECRET", required=True)
engine = build_engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Internal scheduler fires digests per their schedule (admin panel). Disable
    # with SCHEDULER_ENABLED=false (e.g. if you'd rather drive cron.py from host).
    task = stop = None
    if env("SCHEDULER_ENABLED", "true").lower() != "false" and env("GITLAB_URL"):
        gl = GitLabClient(env("GITLAB_URL"), env("GITLAB_TOKEN"))
        stop = asyncio.Event()
        task = asyncio.create_task(run_scheduler(engine, gl, env("GITLAB_GROUP_ID", "3"), stop))
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
    verify_token(x_gitlab_token, SECRET)
    payload = await request.json()

    event = normalize(payload)
    if event is None:
        return {"ignored": f"unhandled event: {payload.get('object_kind')}"}
    return engine.handle(event)
