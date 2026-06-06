"""The single-declaration PASS REGISTRY — the source of truth for every pass.

A "pass" is one notification job: a scheduled digest/reminder (run by the
in-process scheduler or host-cron) or the issue webhook. Everything that used to
be duplicated across cron.py (the run_one if-chain, PASSES/DAILY), settings.py
(PASS_DEFAULTS, HAS_ANCHOR) and the admin UI (titles/descriptions) is declared
ONCE here, so adding a pass is a single REGISTRY entry.

Each `Pass` carries:
  * identity + human strings (name/title/description) — the UI text lives here so
    a later phase can serve it from the backend instead of duplicating in JS;
  * declared metadata (category/trigger/template/event_kind/to/mention/mode/
    daily/schedule_defaults) that the rest of the backend derives from;
  * a thin `run` ADAPTER with a UNIFORM signature that dispatches to the
    underlying digests/reminder function with exactly the kwargs the old
    cron.run_one if-chain assembled — so dispatch is byte-identical.

Import hygiene: this module imports only `digests` (which now also holds the
reminder passes) and `keys`. It must NOT import `settings` or `cron`, because
both of those import THIS module (settings derives PASS_DEFAULTS/HAS_ANCHOR from
the registry; cron derives PASSES/DAILY and dispatches through it) — importing
them back would create a cycle. The one runtime dependency on settings (stale's
`days_idle`) is read off `engine.settings` inside the adapter, not imported.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

import digests
import keys


@dataclass(frozen=True)
class Pass:
    """One notification job — declared once, derived from everywhere.

    Fields:
      name            short id ("team") — the cron subcommand / admin key.
      title           human title (UI).
      description      human description (UI; copied from the old admin.js PASS_INFO).
      category         "group" (room passes) or "personal" (dm passes) — declared
                       metadata for a later UI phase; not used for dispatch yet.
      trigger          "schedule" (the 7 scheduled passes) or "webhook" (issue).
      template         the Jinja template name (templates/<template>.matrix.*.j2).
      event_kind       the Matrix Event.kind this pass emits (STABLE — matches the
                       config.yaml rule `event:` and keys.py constants).
      to               destination tuple: ("room",) or ("dm",).
      mention          who gets @-pinged ("assignee") or None.
      mode             "full" = always a full overview (digest_full/team_full),
                       "delta" = always a delta and the SOLE writer of the delta
                       baseline (digest_delta/team_delta); "single" = one mode
                       always (the per-issue reminders + weekly hygiene passes).
      daily            True if run by a bare `cron.py all` (the daily set);
                       False for weekly-only passes (metrics).
      schedule_defaults the default scheduler config (enabled/days/time [+anchor_days
                       /days_idle]) — EXACTLY the old settings.PASS_DEFAULTS entry.
      run              uniform-signature adapter (None for the webhook pass):
                       run(engine, gl, group_id, issues, store, *, full, dry,
                           commit, room, skey) -> result dict.
    """
    name: str
    title: str
    description: str
    category: str
    trigger: str
    template: str
    event_kind: str
    to: tuple
    mention: str | None
    mode: str
    daily: bool
    schedule_defaults: dict = field(default_factory=dict)
    run: Callable | None = None


WORKDAYS = [0, 1, 2, 3, 4]

# The two DELTA digests run on an INTERVAL within the active-hours window (Phase
# 3a), modelled on Prometheus Alertmanager: `every_hours` is the regular cadence
# ceiling (group_interval) — send at least this often when something changed;
# `change_threshold` sends early on a burst (group_wait-as-threshold);
# `floor_min` is the spam-guard / evaluation throttle (min minutes between
# evaluations and sends). Calendar passes carry no `kind` (treated as "daily").
INTERVAL_DELTA_DEFAULTS = {
    "kind": "interval",
    "every_hours": 2.5,        # cadence ceiling: send at least this often on change
    "floor_min": 20,           # min minutes between evaluations/sends (spam guard)
    "change_threshold": 5,     # send early once >= N items changed since last send
    "days": WORKDAYS,          # workdays only
}


# --- per-pass run adapters -----------------------------------------------
# Each adapter is one branch of the old cron.run_one if-chain. They share the
# UNIFORM signature run(engine, gl, group_id, issues, store, *, full, dry,
# commit, room, skey) and assemble the same kwargs the old chain did (the `ds`
# /`wk` dicts and the anchor->full derivation are reconstructed here, locally,
# so the dispatch is byte-identical).
def _ds_kwargs(*, full, dry, commit, room, skey) -> dict:
    """The kwargs the split digest adapters pass to digests.personal/team.

    `full` here is the pass's FIXED mode — each split pass forces it (digest_full
    -> True, digest_delta -> False), regardless of the scheduler's `anchor` flag
    (the anchor concept is gone for these). We pass `full` explicitly so the
    digest function never re-derives it from an anchor day; `anchor_days` is left
    empty/inert. `skip_weekends=False` mirrors the old run_one (the scheduler's
    own non-working-day gate already silences weekends/holidays).
    """
    return dict(anchor_days=set(), holidays=frozenset(),
                skip_weekends=False, room=room, skey=skey, full=full, dry=dry, commit=commit)


def _wk_kwargs(*, full, dry, commit, room, skey) -> dict:
    """The `wk` dict the old run_one built for triage/stale (weekly passes)."""
    wd = dt.date.today().weekday()
    return dict(weekly_day=wd, holidays=frozenset(), room=room, skey=skey,
                full=full, dry=dry, commit=commit)


def _run_due(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.run_due_soon(engine, issues, store, room=room, skey=skey,
                                commit=commit, dry=dry)


def _run_overdue(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.run_overdue(engine, issues, store, room=room, skey=skey,
                               commit=commit, dry=dry)


# The four split digest adapters FORCE the pass's mode (ignoring the caller's
# `full`/anchor): a digest_full run is always a full overview, a digest_delta run
# is always a delta (and the sole writer of the delta baseline) — see digests.py.
def _run_digest_full(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.personal(engine, issues, store,
                            **_ds_kwargs(full=True, dry=dry, commit=commit, room=room, skey=skey))


def _run_digest_delta(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.personal(engine, issues, store,
                            **_ds_kwargs(full=False, dry=dry, commit=commit, room=room, skey=skey))


def _run_team_full(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.team(engine, issues, store,
                        **_ds_kwargs(full=True, dry=dry, commit=commit, room=room, skey=skey))


def _run_team_delta(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.team(engine, issues, store,
                        **_ds_kwargs(full=False, dry=dry, commit=commit, room=room, skey=skey))


def _run_triage(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.triage(engine, issues, store,
                          **_wk_kwargs(full=full, dry=dry, commit=commit, room=room, skey=skey))


def _run_stale(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    days = int(engine.settings.pass_schedule("stale").get("days_idle", digests.STALE_DAYS))
    return digests.stale(engine, gl, group_id, store, days=days,
                         **_wk_kwargs(full=full, dry=dry, commit=commit, room=room, skey=skey))


def _run_metrics(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.metrics(engine, gl, group_id, store, room=room, skey=skey,
                           full=full, dry=dry, commit=commit)


# --- the registry --------------------------------------------------------
# Order: due, overdue, then the four split digests (digest_full/digest_delta,
# team_full/team_delta — Phase 2a split each anchor-dual digest into a separate
# full and delta pass), triage, stale, metrics + the issue webhook last.
_PASSES = [
    Pass(
        name="due", title="Дедлайн завтра",
        description="Напоминание в общую комнату об issue, у которых срок наступает завтра.",
        category="group", trigger="schedule", template="due_soon", event_kind=keys.DUE_SOON,
        to=("room",), mention="assignee", mode="single", daily=True,
        schedule_defaults={"enabled": True, "days": WORKDAYS, "time": "09:00"},
        run=_run_due,
    ),
    Pass(
        name="overdue", title="Просрочки",
        description="Личное напоминание исполнителю об его issue с прошедшим сроком.",
        category="personal", trigger="schedule", template="overdue", event_kind=keys.OVERDUE,
        to=("dm",), mention="assignee", mode="single", daily=True,
        schedule_defaults={"enabled": True, "days": WORKDAYS, "time": "09:00"},
        run=_run_overdue,
    ),
    Pass(
        name="digest_full", title="Личный дайджест — полный",
        description="Каждому в личку полный обзор его задач, сгруппированный по срокам. По умолчанию по средам и пятницам.",
        category="personal", trigger="schedule", template="digest_personal",
        event_kind=keys.DIGEST_PERSONAL, to=("dm",), mention=None, mode="full", daily=True,
        schedule_defaults={"enabled": True, "days": [2, 4], "time": "09:00"},
        run=_run_digest_full,
    ),
    Pass(
        name="digest_delta", title="Личный дайджест — изменения",
        description="Каждому в личку, что изменилось со вчера: новые/снятые задачи, перенос срока, переход в просрочку. По умолчанию пн/вт/чт.",
        category="personal", trigger="schedule", template="digest_personal",
        event_kind=keys.DIGEST_PERSONAL, to=("dm",), mention=None, mode="delta", daily=True,
        schedule_defaults={"enabled": True, "time": "09:00", **INTERVAL_DELTA_DEFAULTS},
        run=_run_digest_delta,
    ),
    Pass(
        name="team_full", title="Сводка команды — полная",
        description="Полный обзор задач команды в общую комнату: просрочено / сегодня / ближайшие / в работе. По умолчанию по средам и пятницам.",
        category="group", trigger="schedule", template="digest_team",
        event_kind=keys.DIGEST_TEAM, to=("room",), mention=None, mode="full", daily=True,
        schedule_defaults={"enabled": True, "days": [2, 4], "time": "09:30"},
        run=_run_team_full,
    ),
    Pass(
        name="team_delta", title="Сводка команды — изменения",
        description="Что изменилось в задачах команды со вчера → в общую комнату. По умолчанию пн/вт/чт.",
        category="group", trigger="schedule", template="digest_team",
        event_kind=keys.DIGEST_TEAM, to=("room",), mention=None, mode="delta", daily=True,
        schedule_defaults={"enabled": True, "time": "09:30", **INTERVAL_DELTA_DEFAULTS},
        run=_run_team_delta,
    ),
    Pass(
        name="triage", title="Триаж",
        description="Что требует внимания: без исполнителя или без срока. Обычно раз в неделю.",
        category="group", trigger="schedule", template="triage", event_kind=keys.TRIAGE,
        to=("room",), mention=None, mode="single", daily=True,
        schedule_defaults={"enabled": True, "days": [0], "time": "10:00"},
        run=_run_triage,
    ),
    Pass(
        name="stale", title="Зависшие задачи",
        description="Открытые issue без активности ≥ N дней (порог — ниже). Обычно раз в неделю.",
        category="group", trigger="schedule", template="stale", event_kind=keys.STALE,
        to=("room",), mention=None, mode="single", daily=True,
        schedule_defaults={"enabled": True, "days": [0], "time": "10:00", "days_idle": 14},
        run=_run_stale,
    ),
    Pass(
        name="metrics", title="Метрики потока",
        description="Еженедельный снимок: закрыто / в работе (WIP) / возраст / cycle time p85.",
        category="group", trigger="schedule", template="metrics", event_kind=keys.METRICS,
        to=("room",), mention=None, mode="single", daily=False,
        schedule_defaults={"enabled": False, "days": [0], "time": "10:00"},
        run=_run_metrics,
    ),
    Pass(
        name="issue", title="Открытие / закрытие issue",
        description="Issue открыли, закрыли или переоткрыли (вебхук) → в общую комнату.",
        category="group", trigger="webhook", template="issue", event_kind="issue",
        to=("room",), mention="assignee", mode="single", daily=False,
        schedule_defaults={}, run=None,
    ),
]

REGISTRY: dict[str, Pass] = {p.name: p for p in _PASSES}


# --- derived views (the rest of the backend consumes these) --------------
def scheduled() -> tuple[str, ...]:
    """Scheduled-pass names, registry order — the old cron.PASSES."""
    return tuple(p.name for p in REGISTRY.values() if p.trigger == "schedule")


def daily() -> tuple[str, ...]:
    """Daily-set scheduled-pass names — the old cron.DAILY."""
    return tuple(p.name for p in REGISTRY.values() if p.trigger == "schedule" and p.daily)


def pass_defaults() -> dict:
    """{name: schedule_defaults} for scheduled passes — the old settings.PASS_DEFAULTS."""
    return {p.name: dict(p.schedule_defaults) for p in REGISTRY.values() if p.trigger == "schedule"}


def has_anchor() -> tuple[str, ...]:
    """Anchor-dual passes — empty since Phase 2a split digest/team into separate
    full + delta passes (no pass is anchor-dual anymore). Kept (returning ()) for
    back-compat with the admin payload until the Phase 4 UI cleanup."""
    return ()
