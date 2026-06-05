"""In-process scheduler: the bot fires its own digests on a per-pass schedule
(days + time set in the admin panel), so no host cron is needed — the webhook
service is already always-on.

Every ~30s it checks each pass: enabled? today in its days? time reached? not
already run today? not a non-working day? — then runs it once. Digests send a
full overview on their anchor days, a delta otherwise.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

import cron

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
    """Should this pass fire right now? (enabled, today, within its grace window)"""
    if not cfg.get("enabled"):
        return False
    if weekday not in cfg.get("days", []):
        return False
    t = _hhmm_to_min(cfg.get("time", "09:00"))
    return t <= now_min <= t + GRACE_MINUTES


def tick(engine, gl, group_id: str) -> None:
    s = engine.settings
    if not s.enabled():                          # global kill switch
        return
    now = dt.datetime.now()
    today = now.date()
    iso = today.isoformat()
    now_min = now.hour * 60 + now.minute
    store = s.store
    if s.is_nonworking(iso):                      # holidays / weekends -> silent
        return
    for name in cron.PASSES:
        cfg = s.pass_schedule(name)
        if not due_now(cfg, today.weekday(), now_min):
            continue
        if store.already_sent("sched", name, day=iso):   # already fired today
            continue
        anchor = today.weekday() in cfg.get("anchor_days", [])
        try:
            n = cron.run_one(engine, gl, group_id, store, name, anchor=anchor)
            store.mark_sent("sched", name, day=iso)
            log.info("scheduled pass %s fired -> %s sent", name, n)
        except Exception:                          # noqa: BLE001 — one pass mustn't kill the loop
            log.exception("scheduled pass %s failed", name)


async def run_scheduler(engine, gl, group_id: str, stop: asyncio.Event) -> None:
    log.info("scheduler started (tick %ss)", TICK_SECONDS)
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick, engine, gl, group_id)
        except Exception:                          # noqa: BLE001
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("scheduler stopped")
