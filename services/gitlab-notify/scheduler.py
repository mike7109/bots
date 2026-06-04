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


def tick(engine, gl, group_id: str) -> None:
    s = engine.settings
    if not s.enabled():                          # global kill switch
        return
    now = dt.datetime.now()
    today = now.date()
    iso = today.isoformat()
    hhmm = now.strftime("%H:%M")
    store = s.store
    if s.is_nonworking(iso):                      # holidays / weekends -> silent
        return
    for name in cron.PASSES:
        cfg = s.pass_schedule(name)
        if not cfg.get("enabled"):
            continue
        if today.weekday() not in cfg.get("days", []):
            continue
        if hhmm < cfg.get("time", "09:00"):       # not time yet today
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
