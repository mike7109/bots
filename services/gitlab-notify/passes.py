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
      mode             "auto" = anchor-dual (full on anchor day, delta otherwise:
                       digest/team); "single" = one mode always (the rest).
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


# --- per-pass run adapters -----------------------------------------------
# Each adapter is one branch of the old cron.run_one if-chain. They share the
# UNIFORM signature run(engine, gl, group_id, issues, store, *, full, dry,
# commit, room, skey) and assemble the same kwargs the old chain did (the `ds`
# /`wk` dicts and the anchor->full derivation are reconstructed here, locally,
# so the dispatch is byte-identical).
def _ds_kwargs(*, full, dry, commit, room, skey) -> dict:
    """The `ds` dict the old run_one built for the two delta digests.

    `full` here is ALREADY the resolved mode (`is_full`): run_one folds the
    scheduler's `anchor` flag into it (`full = full if full is not None else
    anchor`) before dispatch. The old code passed `anchor_days={wd}` when full
    else `set()`; the digest functions re-derive `is_full = full if full is not
    None else (wd in anchor_days)`, which here collapses to exactly `full`, so
    the effective full/delta mode is byte-identical to before.
    """
    wd = dt.date.today().weekday()
    return dict(anchor_days={wd} if full else set(), holidays=frozenset(),
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


def _run_digest(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.personal(engine, issues, store,
                            **_ds_kwargs(full=full, dry=dry, commit=commit, room=room, skey=skey))


def _run_team(engine, gl, group_id, issues, store, *, full, dry, commit, room, skey):
    return digests.team(engine, issues, store,
                        **_ds_kwargs(full=full, dry=dry, commit=commit, room=room, skey=skey))


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
# Ordered to preserve the old cron.PASSES order (due, overdue, digest, team,
# triage, stale, metrics) + the issue webhook last.
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
        name="digest", title="Личный дайджест «Задачи на сегодня»",
        description="Каждому в личку его задачи. В якорные дни — полный обзор, в остальные — только изменения со вчера.",
        category="personal", trigger="schedule", template="digest_personal",
        event_kind=keys.DIGEST_PERSONAL, to=("dm",), mention=None, mode="auto", daily=True,
        schedule_defaults={"enabled": True, "days": WORKDAYS, "time": "09:00", "anchor_days": [2, 4]},
        run=_run_digest,
    ),
    Pass(
        name="team", title="Сводка команды",
        description="Обзор задач команды в общую комнату: просрочено / сегодня / ближайшие / в работе.",
        category="group", trigger="schedule", template="digest_team",
        event_kind=keys.DIGEST_TEAM, to=("room",), mention=None, mode="auto", daily=True,
        schedule_defaults={"enabled": True, "days": WORKDAYS, "time": "09:30", "anchor_days": [2, 4]},
        run=_run_team,
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
    """Scheduled passes whose mode is anchor-dual — the old settings.HAS_ANCHOR."""
    return tuple(p.name for p in REGISTRY.values() if p.trigger == "schedule" and p.mode == "auto")
