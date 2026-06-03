"""GitLab webhook receiver: expands child Tasks of an issue into Issues.

Trigger: a comment `/expand-tasks` (dry-run) or `/expand-tasks confirm` (apply)
on any Issue. The actual work is done in expand.py.
"""
import hmac
import logging
import os
import threading

from fastapi import FastAPI, Header, HTTPException, Request

from expand import dry_run, member_can, post_note, run

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("expand-tasks")


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val


# Fail fast on misconfiguration instead of 500-ing on the first webhook.
SECRET = _required("WEBHOOK_SECRET")
BOT_ID = int(_required("BOT_USER_ID"))   # token bot's user id -> anti-loop guard
_required("GITLAB_URL")
_required("GITLAB_TOKEN")
MIN_ROLE = int(os.environ.get("MIN_ROLE", "30"))   # 30 = Developer, 40 = Maintainer

app = FastAPI(title="gitlab-webhook-expand-tasks")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, x_gitlab_token: str = Header(default="")):
    if not hmac.compare_digest(x_gitlab_token, SECRET):
        raise HTTPException(status_code=403, detail="bad webhook token")

    payload = await request.json()
    if payload.get("object_kind") != "note":
        return {"ignored": "not a note event"}

    attr = payload["object_attributes"]
    if attr.get("noteable_type") != "Issue":
        return {"ignored": "note is not on an issue"}

    author = attr["author_id"]
    note = (attr.get("note") or "").strip()

    if author == BOT_ID:                       # our own report comment -> stop
        return {"ignored": "self-authored note"}
    if not note.startswith("/expand-tasks"):
        return {"ignored": "no command"}

    project_id = payload["project"]["id"]
    iid = str(payload["issue"]["iid"])
    path = payload["project"]["path_with_namespace"]
    confirm = note.split()[1:2] == ["confirm"]

    if not member_can(project_id, author, MIN_ROLE):
        post_note(
            project_id, iid,
            f"❌ `/expand-tasks`: недостаточно прав "
            f"(нужен access level ≥ {MIN_ROLE}).",
        )
        return {"ignored": "insufficient permissions"}

    # GitLab webhook timeout is ~10s; do the work in the background and
    # answer immediately so the hook does not get marked as failing.
    target = run if confirm else dry_run
    mode = "run" if confirm else "dry-run"
    log.info("accepted /expand-tasks (%s) for %s#%s by user %s",
             mode, path, iid, author)
    threading.Thread(
        target=target, args=(path, project_id, iid), daemon=True,
    ).start()
    return {"accepted": True, "mode": mode}
