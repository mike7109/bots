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
import passes
import subtasks

log = logging.getLogger("gitlab-notify.scheduler")
TICK_SECONDS = 30
# A pass fires only within this many minutes after its scheduled time. So a
# fresh deploy (empty ledger) at, say, 15:00 does NOT "catch up" and blast every
# morning pass at once — it just waits for the next scheduled slot. A normal
# restart a few minutes late still fires.
GRACE_MINUTES = 120

# Per-(source,pass) last-evaluation timestamp for INTERVAL passes (Phase 3a).
# In-memory only: an interval pass self-silences (no changes -> no send), so a
# restart simply re-evaluates one tick sooner — there is nothing to lose. The
# SEND cadence/anti-spam is governed by the persistent last-SEND record
# (store.last_run) + the delta baseline, not by this throttle.
_LAST_EVAL: dict[str, dt.datetime] = {}


def _reset_interval_state() -> None:
    """Drop the in-memory eval-throttle (tests want a clean slate)."""
    _LAST_EVAL.clear()


def interval_should_send(*, has_changes: bool, n_changes: int,
                         elapsed_since_send_h: float | None,
                         every_hours: float, change_threshold: int,
                         in_quiet_after_full: bool) -> bool:
    """Should an INTERVAL delta pass SEND right now? (pure decision, no I/O)

    Modelled on Alertmanager: SEND iff there are changes AND we're not inside the
    post-full quiet window AND either the regular cadence ceiling elapsed
    (`elapsed_since_send_h >= every_hours`) OR a burst accumulated
    (`n_changes >= change_threshold`, early send). `elapsed_since_send_h is None`
    means the pass has never sent before -> the cadence ceiling is considered
    elapsed (send on the first change). No changes -> never send; inside the
    quiet-after-full window -> never send (the full just covered it)."""
    if not has_changes:
        return False
    if in_quiet_after_full:
        return False
    cadence_elapsed = elapsed_since_send_h is None or elapsed_since_send_h >= every_hours
    burst = n_changes >= change_threshold
    return cadence_elapsed or burst


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


def _last_full_marker_key(name: str) -> str:
    """The state key (within keys.ns(skey, ...)) a *_full send stamps and the
    intraday *_delta reads for the overlap guard — the pass's shared event_kind
    (digest_personal / digest_team), so full+delta of one digest share it."""
    return passes.REGISTRY[name].event_kind


def _in_quiet_after_full(store, skey: str, event_kind: str, now: dt.datetime,
                         quiet_h: float) -> bool:
    """Did a *_full of this digest send within the last `quiet_h` hours? (overlap
    guard). 0/absent marker -> not quiet. A malformed marker fails OPEN (not
    quiet) so a corrupt row can't permanently mute the delta."""
    if quiet_h <= 0:
        return False
    marker = store.get_state("last_full", keys.ns(skey, event_kind))
    ts = (marker or {}).get("ts")
    if not ts:
        return False
    try:
        last = dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return False
    return (now - last) < dt.timedelta(hours=quiet_h)


def _run_calendar_pass(ctx, store, gl, gid, src, name, cfg, today, iso, now_min) -> None:
    """The daily/calendar path: pass_due + grace + SCHED day-ledger + record_run.
    UNCHANGED from the pre-Phase-3a logic — every non-interval pass uses this."""
    schedkey = keys.ns(src["id"], name)
    # A prior run from an EARLIER day marks an established schedule that may
    # recover a slot missed across downtime; a run already recorded for today (or
    # none at all) does not warrant a past-grace catch-up.
    prior = store.last_run(schedkey)
    has_prior_run = bool(prior and prior.get("iso") and prior["iso"] < iso)
    if not pass_due(cfg, today.weekday(), now_min, has_prior_run=has_prior_run):
        return
    if store.already_sent(keys.SCHED, schedkey, day=iso):   # already fired today (this source)
        return
    # `anchor` is now inert for the digest passes — Phase 2a split them into
    # separate full/delta passes whose adapters FORCE the mode, and no pass
    # carries `anchor_days` anymore (so this is always False). Kept for the
    # uniform run_one signature.
    anchor = today.weekday() in cfg.get("anchor_days", [])
    try:
        result = cron.run_one(ctx.engine, gl, gid, store, name,
                              anchor=anchor, room=src.get("room"), skey=src["id"])
        store.mark_sent(keys.SCHED, schedkey, day=iso)
        store.record_run(schedkey, iso, ok=True)
        # Overlap guard: when a *_full digest actually SENDS, stamp a "last_full"
        # marker (shared event_kind key) so the intraday *_delta can quiet itself
        # for a few hours and not restate what the full just showed.
        if passes.REGISTRY[name].mode == "full" and result.get("sent"):
            store.set_state("last_full", keys.ns(src["id"], _last_full_marker_key(name)),
                            {"ts": dt.datetime.now().isoformat(timespec="seconds")})
        log.info("scheduled %s/%s fired -> %s sent", src["id"], name, result["sent"])
    except Exception as e:                         # noqa: BLE001 — one pass mustn't kill the loop
        store.record_run(schedkey, iso, ok=False, detail=str(e))
        log.exception("scheduled pass %s/%s failed", src["id"], name)
        _alert(ctx, src["id"], name, e, iso)


def _run_interval_pass(ctx, store, gl, gid, src, name, cfg, today, now) -> None:
    """The INTERVAL path (the two delta digests): evaluate at most every
    `floor_min` min; SEND when there are changes AND (the `every_hours` cadence
    ceiling elapsed OR a `change_threshold` burst accumulated), unless a recent
    *_full quieted it. NO SCHED day-ledger — the delta self-silences and the
    last-SEND timestamp + baseline govern cadence."""
    if today.weekday() not in cfg.get("days", []):     # interval workdays gate
        return
    schedkey = keys.ns(src["id"], name)
    floor_min = float(cfg.get("floor_min", 20))
    last_eval = _LAST_EVAL.get(schedkey)
    if last_eval is not None and (now - last_eval) < dt.timedelta(minutes=floor_min):
        return                                          # evaluation throttle (spam guard)
    _LAST_EVAL[schedkey] = now                          # mark evaluated even if we hold/skip

    every_hours = float(cfg.get("every_hours", 2.5))
    change_threshold = int(cfg.get("change_threshold", 5))
    quiet_h = float(ctx.settings.get_global().get("delta_quiet_after_full_h", 3) or 0)
    event_kind = passes.REGISTRY[name].event_kind
    in_quiet = _in_quiet_after_full(store, src["id"], event_kind, now, quiet_h)

    # last SEND (record_run stores ts on the success path) -> hours since.
    prior = store.last_run(schedkey)
    last_send_ts = (prior or {}).get("ts")
    elapsed_h: float | None = None
    if last_send_ts:
        try:
            elapsed_h = (now - dt.datetime.fromisoformat(last_send_ts)).total_seconds() / 3600.0
        except (TypeError, ValueError):
            elapsed_h = None                            # bad ts -> treat as "never" (cadence elapsed)

    try:
        # Cheap DRY pass to learn reason + change count WITHOUT sending or touching
        # the baseline. reason=="ok" means there are real changes to deliver.
        probe = cron.run_one(ctx.engine, gl, gid, store, name,
                             dry=True, commit=False, room=src.get("room"), skey=src["id"])
        has_changes = probe.get("reason") == "ok"
        n_changes = int(probe.get("n_changes", 0))
        if not interval_should_send(
                has_changes=has_changes, n_changes=n_changes,
                elapsed_since_send_h=elapsed_h, every_hours=every_hours,
                change_threshold=change_threshold, in_quiet_after_full=in_quiet):
            return                                      # nothing to send / hold / quiet
        # Decision made: do the REAL run (sends + advances baseline + records run).
        result = cron.run_one(ctx.engine, gl, gid, store, name,
                              commit=True, room=src.get("room"), skey=src["id"])
        store.record_run(schedkey, today.isoformat(), ok=True)  # ts=now -> next cadence anchor
        log.info("interval %s/%s fired (n=%d, elapsed=%s) -> %s sent",
                 src["id"], name, n_changes, elapsed_h, result["sent"])
    except Exception as e:                              # noqa: BLE001 — one pass mustn't kill the loop
        store.record_run(schedkey, today.isoformat(), ok=False, detail=str(e))
        log.exception("interval pass %s/%s failed", src["id"], name)
        _alert(ctx, src["id"], name, e, today.isoformat())


def _alert(ctx, src_id: str, name: str, e: Exception, iso: str) -> None:
    """DM engineers about a pass failure — must NEVER break the scheduler loop."""
    try:
        ctx.alerter.alert_pass_failure(src_id, name, e, day=iso)
    except Exception:                                   # noqa: BLE001
        log.exception("operator alert failed for %s/%s", src_id, name)


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
    if not s.is_active_now(now):                  # outside the active-hours window -> silent
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
            if not cfg.get("enabled"):
                continue
            # Branch on the pass's schedule kind: the two delta digests run on an
            # interval within the active window; everything else is daily/calendar.
            if cfg.get("kind") == "interval":
                _run_interval_pass(ctx, store, gl, gid, src, name, cfg, today, now)
            else:
                _run_calendar_pass(ctx, store, gl, gid, src, name, cfg, today, iso, now_min)


async def run_scheduler(ctx, stop: asyncio.Event) -> None:
    log.info("scheduler started (tick %ss)", TICK_SECONDS)
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick, ctx)
        except Exception:                          # noqa: BLE001
            log.exception("scheduler tick failed")
        try:
            # Watched-issue subtask poll (GitLab sends no Task webhooks). Runs on
            # its own cadence OUTSIDE tick's calendar gates (active hours/weekends)
            # — it substitutes for webhooks, which stay realtime, not for digests.
            await asyncio.to_thread(subtasks.maybe_poll, ctx)
        except Exception:                          # noqa: BLE001
            log.exception("subtask poll failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("scheduler stopped")
