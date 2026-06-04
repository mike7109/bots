"""GitLab webhook -> Matrix notifications.

What gets sent and how it looks is entirely in config.yaml + templates/ —
this file only verifies the webhook, normalizes the payload, and hands the
event to the engine.
"""
import logging

from fastapi import FastAPI, Header, Request

from botkit.config import env
from botkit.webhook import verify_token
from admin import create_admin_router
from normalize import normalize
from wiring import build_engine

logging.basicConfig(
    level=env("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

SECRET = env("WEBHOOK_SECRET", required=True)
engine = build_engine()

app = FastAPI(title="gitlab-notify")
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
