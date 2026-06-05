"""Daily poll for things webhooks can't tell us about.

Webhooks only cover issue open/close/reopen. Everything date-driven or
aggregated has no event to hang off, so we poll the API. Each subcommand is one
pass; `all` runs the daily set (metrics is weekly, so it's opt-in):

    python cron.py            # all daily passes
    python cron.py due        # due tomorrow -> room
    python cron.py overdue    # past due -> personal DM (deduped)
    python cron.py digest     # personal "what's on me" DM per assignee
    python cron.py team       # standup overview -> room
    python cron.py triage     # untriaged issues -> room
    python cron.py stale      # issues with no activity for N days -> room
    python cron.py metrics    # weekly issue-flow snapshot -> room

Everything dedups through botkit.store so a second run the same day is a no-op
(see Store and digests.py). This is also the right place to reconcile Task
open/close/reopen later, since GitLab 17.6 doesn't webhook Task work items.
"""
import datetime as dt
import logging
import sys

import digests
import keys
import passes
from botkit.config import env
from botkit.gitlab import GitLabClient
from wiring import build_context

log = logging.getLogger("gitlab-notify.cron")

# Scheduled passes + the daily subset, DERIVED from the single pass registry
# (passes.py) so adding a pass is one entry there. Kept under these names/values
# for every existing caller (scheduler, admin, main below).
PASSES = passes.scheduled()
# Daily passes run by a bare `python cron.py`. `metrics` is weekly -> not here.
DAILY = passes.daily()

# The two per-issue reminders moved into digests.py (so the registry imports a
# single module). Re-exported here for backward-compatible callers/tests.
run_due_soon = digests.run_due_soon
run_overdue = digests.run_overdue


def run_one(engine, gl, group_id: str, store, name: str, *,
            anchor: bool = False, full: bool | None = None, dry: bool = False,
            commit: bool = True, room=None, skey: str = "") -> dict:
    """Run a single pass by name. Shared by the scheduler and the admin "send now".

    `room`/`skey` route this source's events to its room and namespace dedup so
    groups don't collide.

    Timing/mode:
      * `anchor` — scheduled anchor day; digests send a full overview (delta
        otherwise). The scheduler/host-cron pass this and leave `full=None`.
      * `full` — explicit operator override of the delta/full mode for the two
        delta digests (`digest`/`team`); None derives it from `anchor`. Ignored
        by single-mode passes.

    State/preview:
      * `commit=True` (default — scheduler/host-cron) — keep today's per-pass
        dedup + baseline writes (byte-identical to before).
      * `commit=False` (manual) — stateless: bypass the dedup gate and write no
        `sent`/`state` rows, so a manual run is repeatable and can't perturb the
        scheduler.
      * `dry=True` — compute + render a real-data sample, do NOT dispatch.

    Returns the uniform result dict: `{"sent": int, "reason": str,
    "recipients": list, "html": str|None}`. The scheduler/host-cron read
    `result["sent"]` for logging/marking, so their behaviour is unchanged.
    """
    p = passes.REGISTRY.get(name)
    if p is None or p.run is None:
        raise ValueError(f"unknown pass: {name}")
    issues = gl.group_issues(group_id, state="opened", scope="all")
    # Fold the scheduler's anchor flag into `full` (old: is_full = full if full
    # is not None else anchor). Single-mode passes ignore `full`; the two delta
    # digests use it to pick full-overview vs delta — byte-identical to before.
    full = full if full is not None else anchor
    return p.run(engine, gl, group_id, issues, store,
                 full=full, dry=dry, commit=commit, room=room, skey=skey)


def main(argv: list[str]) -> None:
    args = list(argv)
    force = "--force" in args                  # let host-cron override the owner check
    args = [a for a in args if a != "--force"]
    cmd = args[0] if args else "all"
    if cmd not in ("all", *PASSES):
        sys.exit(f"usage: cron.py [--force] [{'|'.join(PASSES)}]   (got {cmd!r})")

    ctx = build_context()
    store = ctx.store                         # shared DB (dedup + settings live together)
    settings = ctx.settings
    today = dt.date.today()
    today_iso = today.isoformat()
    wanted = DAILY if cmd == "all" else (cmd,)
    url = settings.conn_value("gitlab_url")

    # Single schedule owner: if «Авторассылки» (the in-process scheduler) is on,
    # IT owns the schedule. Host-cron is only meant for when that's off — running
    # both would double-send in the network window. So bail out (exit 0) unless
    # --force is passed.
    if settings.get_global().get("scheduler_on", True) and not force:
        log.warning("scheduler_on=True: in-process scheduler owns the schedule; "
                    "host-cron exiting without sending (use --force to override)")
        store.close()
        return

    # Host-cron fallback (when «Авторассылки» is off, or forced): run the
    # requested passes for every configured source. Even when forced we honour +
    # write the SAME `sched` ledger the scheduler uses, so the two coordinate and
    # never double-send the same pass on the same day. Digests send full on a
    # pass's anchor day, a delta otherwise.
    try:
        for src in ctx.sources.enabled():
            gid = src.get("group_id")
            if not gid:
                continue
            gl = GitLabClient(url, src.get("token", ""))
            for name in wanted:
                schedkey = keys.ns(src["id"], name)
                if store.already_sent(keys.SCHED, schedkey, day=today_iso):
                    log.info("cron %s/%s: already fired today — skip", src["id"], name)
                    continue
                anchor = today.weekday() in settings.pass_schedule(name).get("anchor_days", [])
                run_one(ctx.engine, gl, gid, store, name,
                        anchor=anchor, room=src.get("room"), skey=src["id"])
                store.mark_sent(keys.SCHED, schedkey, day=today_iso)
                store.record_run(schedkey, today_iso, ok=True)
    finally:
        store.close()


if __name__ == "__main__":
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    main(sys.argv[1:])
