"""In-process scheduler: the bot fires its own digests on a per-pass schedule
(days + time set in the admin panel), so no host cron is needed — the webhook
service is already always-on.

Every ~30s it checks each pass: enabled? today in its days? time reached? not
already run today? not a non-working day? — then runs it once. Digests send a
full overview on their anchor days, a delta otherwise.

If the process was down across a pass's whole grace window, an ESTABLISHED
schedule (one with a prior run on an earlier day) still recovers the missed slot
when it comes back up; a FRESH deploy with no run history does not catch up (no
morning-pass storm) — see `pass_due`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from botkit.gitlab import GitLabClient

import cron
import keys

log = logging.getLogger("gitlab-notify.scheduler")
TICK_SECONDS = 30
# A pass fires only within this many minutes after its scheduled time. So a
# fresh deploy (empty ledger) at, say, 15:00 does NOT "catch up" and blast every
# morning pass at once — it just waits for the next scheduled slot. A normal
# restart a few minutes late still fires.
GRACE_MINUTES = 120


def _hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def due_now(cfg: dict, weekday: int, now_min: int) -> bool:
    """Should this pass fire right now? (enabled, today, within its grace window)

    The grace window is the ON-TIME / slightly-late branch only. Recovery of a
    run missed across a longer downtime is handled by `pass_due` using run
    history — see there.
    """
    if not cfg.get("enabled"):
        return False
    if weekday not in cfg.get("days", []):
        return False
    t = _hhmm_to_min(cfg.get("time", "09:00"))
    return t <= now_min <= t + GRACE_MINUTES


def pass_due(cfg: dict, weekday: int, now_min: int, *, has_prior_run: bool) -> bool:
    """Should this pass fire now, accounting for downtime recovery?

    Three branches (enabled + today-in-days already required):
      * before the scheduled time            -> not due
      * within the grace window (<= t+GRACE)  -> due (on-time / slightly late)
      * past the grace window                 -> due ONLY IF there's a prior run
        record from an earlier day, i.e. an ESTABLISHED schedule that missed
        today's slot (recover it). A fresh deploy (no prior run) does NOT catch
        up — it waits for the next on-time slot. This keeps the anti-storm
        behaviour for fresh deploys while still recovering a real missed run.
    The per-day `already_sent("sched", ...)` guard (in `tick`) still ensures one
    fire per day; this only decides timing.
    """
    if not cfg.get("enabled"):
        return False
    if weekday not in cfg.get("days", []):
        return False
    t = _hhmm_to_min(cfg.get("time", "09:00"))
    if now_min < t:
        return False
    if now_min <= t + GRACE_MINUTES:
        return True                              # on-time / slightly late
    return has_prior_run                         # past grace: recover only if established


def tick(ctx) -> None:
    s = ctx.settings
    if not s.enabled() or not s.get_global().get("scheduler_on", True):
        return                                   # kill switch / scheduler off (admin)
    now = dt.datetime.now()
    today = now.date()
    iso = today.isoformat()
    now_min = now.hour * 60 + now.minute
    store = ctx.store
    if s.is_nonworking(iso):                      # holidays / weekends -> silent
        return
    gitlab_url = s.conn_value("gitlab_url")
    # Each source = its own group + token + room; iterate them all.
    for src in ctx.sources.enabled():
        gid = src.get("group_id")
        if not gid:
            continue
        gl = GitLabClient(gitlab_url, src.get("token", ""))
        for name in cron.PASSES:
            cfg = s.pass_schedule(name)
            schedkey = keys.ns(src["id"], name)
            # A prior run from an EARLIER day marks an established schedule that
            # may recover a slot missed across downtime; a run already recorded
            # for today (or none at all) does not warrant a past-grace catch-up.
            prior = store.last_run(schedkey)
            has_prior_run = bool(prior and prior.get("iso") and prior["iso"] < iso)
            if not pass_due(cfg, today.weekday(), now_min, has_prior_run=has_prior_run):
                continue
            if store.already_sent(keys.SCHED, schedkey, day=iso):   # already fired today (this source)
                continue
            anchor = today.weekday() in cfg.get("anchor_days", [])
            try:
                result = cron.run_one(ctx.engine, gl, gid, store, name,
                                      anchor=anchor, room=src.get("room"), skey=src["id"])
                store.mark_sent(keys.SCHED, schedkey, day=iso)
                store.record_run(schedkey, iso, ok=True)
                log.info("scheduled %s/%s fired -> %s sent", src["id"], name, result["sent"])
            except Exception as e:                 # noqa: BLE001 — one pass mustn't kill the loop
                store.record_run(schedkey, iso, ok=False, detail=str(e))
                log.exception("scheduled pass %s/%s failed", src["id"], name)
                try:                               # DM engineers — must NEVER break the loop
                    ctx.alerter.alert_pass_failure(src["id"], name, e, day=iso)
                except Exception:                  # noqa: BLE001
                    log.exception("operator alert failed for %s/%s", src["id"], name)


async def run_scheduler(ctx, stop: asyncio.Event) -> None:
    log.info("scheduler started (tick %ss)", TICK_SECONDS)
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick, ctx)
        except Exception:                          # noqa: BLE001
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("scheduler stopped")
