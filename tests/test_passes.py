"""passes: the single-declaration PASS REGISTRY is the source of truth.

These pin that the registry (a) covers exactly the scheduled passes — now with
the FOUR split digests (digest_full/digest_delta, team_full/team_delta) and no
anchor-dual digest/team — plus the issue webhook, (b) reproduces the
PASS_DEFAULTS the rest of the backend derives, with HAS_ANCHOR now empty,
(c) exposes a uniform-signature, callable `run` adapter for each scheduled pass,
and (d) each split adapter FORCES its mode through run_one (full overview vs
delta), with the full pass read-only and the delta pass owning the baseline.
"""
from __future__ import annotations

import datetime as dt

import cron
import keys
import passes
import settings
from botkit.store import Store

# The schedule defaults the backend derives from the registry (settings
# .PASS_DEFAULTS / cron.PASSES / DAILY). Phase 2a: digest/team are gone, replaced
# by the four split passes — full on Wed/Fri, delta on Mon/Tue/Thu.
WORKDAYS = [0, 1, 2, 3, 4]
# Phase 3a: the two DELTA digests carry an INTERVAL schedule (kind="interval" +
# every_hours/floor_min/change_threshold, days=WORKDAYS); the others stay daily.
_INTERVAL = {"kind": "interval", "every_hours": 2.5, "floor_min": 20,
             "change_threshold": 5, "days": WORKDAYS}
EXPECTED_PASS_DEFAULTS = {
    "due":          {"enabled": True,  "days": WORKDAYS,  "time": "09:00"},
    "overdue":      {"enabled": True,  "days": WORKDAYS,  "time": "09:00"},
    "digest_full":  {"enabled": True,  "days": [2, 4],    "time": "09:00"},
    "digest_delta": {"enabled": True,  "time": "09:00", **_INTERVAL},
    "team_full":    {"enabled": True,  "days": [2, 4],    "time": "09:30"},
    "team_delta":   {"enabled": True,  "time": "09:30", **_INTERVAL},
    "triage":       {"enabled": True,  "days": [0],       "time": "10:00"},
    "stale":        {"enabled": True,  "days": [0],       "time": "10:00", "days_idle": 14},
    "metrics":      {"enabled": False, "days": [0],       "time": "10:00"},
}
EXPECTED_PASSES = ("due", "overdue", "digest_full", "digest_delta",
                   "team_full", "team_delta", "triage", "stale", "metrics")
EXPECTED_DAILY = ("due", "overdue", "digest_full", "digest_delta",
                  "team_full", "team_delta", "triage", "stale")


# --- (a) coverage --------------------------------------------------------
def test_registry_covers_scheduled_passes_plus_issue_webhook():
    # Registry order is preserved; scheduled subset == cron.PASSES; the one
    # webhook entry is "issue" (no schedule). The old anchor-dual digest/team are
    # gone, replaced by the four split passes.
    assert list(passes.REGISTRY) == [*EXPECTED_PASSES, "issue"]
    assert passes.scheduled() == EXPECTED_PASSES == cron.PASSES
    assert passes.daily() == EXPECTED_DAILY == cron.DAILY
    assert "digest" not in passes.REGISTRY and "team" not in passes.REGISTRY
    issue = passes.REGISTRY["issue"]
    assert issue.trigger == "webhook"
    assert issue.run is None
    assert issue.schedule_defaults == {}
    assert "issue" not in passes.scheduled()


# --- (a2) the four split passes have the right metadata ------------------
def test_split_passes_metadata():
    # event_kind stays STABLE (full and delta share one kind) so templates,
    # config.yaml rules, routing and by_kind stats are unchanged; only the PASS
    # entity split. `to`/`category`/`mode` per the design.
    R = passes.REGISTRY
    assert R["digest_full"].event_kind == R["digest_delta"].event_kind == keys.DIGEST_PERSONAL
    assert R["team_full"].event_kind == R["team_delta"].event_kind == keys.DIGEST_TEAM
    for n in ("digest_full", "digest_delta"):
        assert R[n].to == ("dm",) and R[n].category == "personal"
    for n in ("team_full", "team_delta"):
        assert R[n].to == ("room",) and R[n].category == "group"
    assert R["digest_full"].mode == R["team_full"].mode == "full"
    assert R["digest_delta"].mode == R["team_delta"].mode == "delta"
    # template == event_kind for all four.
    for n in ("digest_full", "digest_delta", "team_full", "team_delta"):
        assert R[n].template == R[n].event_kind


# --- (b) derived defaults equal the expected values ----------------------
def test_pass_defaults_derived_from_registry_match_old():
    assert passes.pass_defaults() == EXPECTED_PASS_DEFAULTS
    assert settings.PASS_DEFAULTS == EXPECTED_PASS_DEFAULTS


def test_has_anchor_is_empty_after_split():
    # No pass is anchor-dual anymore: has_anchor()/HAS_ANCHOR are empty, and no
    # registry entry uses mode "auto".
    assert passes.has_anchor() == ()
    assert settings.HAS_ANCHOR == ()
    assert tuple(p.name for p in passes.REGISTRY.values() if p.mode == "auto") == ()


# --- (c) every scheduled pass's run adapter is callable -------------------
class _Settings:
    """Minimal settings stub: stale's adapter reads days_idle off engine.settings."""
    def pass_schedule(self, name):
        return {"days_idle": 14}


class _Engine:
    """Engine fake: handle + match + renderer, plus a settings for stale."""
    def __init__(self):
        self.handled: list = []
        self.renderer = self
        self.settings = _Settings()

    def handle(self, event):
        self.handled.append(event)
        return {"sent": ["room"]}

    def match(self, event):
        return {"template": event.kind}

    def render(self, template, channel, ctx):
        return f"<{template}>"


class _GitLab:
    """GitLab client fake: returns a fixed `opened` issue list for every query."""
    def __init__(self, issues):
        self._issues = issues

    def group_issues(self, *a, **kw):
        return self._issues


def _issue(iid, *, gid=None, due=None):
    return {"id": gid if gid is not None else iid, "iid": iid, "due_date": due,
            "title": f"issue {iid}", "web_url": f"u/{iid}",
            "updated_at": "2000-01-01T00:00:00Z", "created_at": "2000-01-01T00:00:00Z"}


def test_each_scheduled_run_adapter_is_callable_with_uniform_signature(tmp_path):
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    for name in passes.scheduled():
        p = passes.REGISTRY[name]
        assert callable(p.run), name
        store = Store(path=str(tmp_path / f"{name}.db"))
        engine = _Engine()
        gl = _GitLab([_issue(1, gid=11, due=yesterday)])
        issues = gl.group_issues("g1", state="opened", scope="all")
        # Uniform signature: keyword-only full/dry/commit/room/skey.
        result = p.run(engine, gl, "g1", issues, store,
                       full=None, dry=False, commit=False, room="!r", skey="s1")
        assert set(result) == {"sent", "reason", "recipients", "html", "n_changes"}, name
        store.close()


# --- (d) split-pass dispatch: the adapter FORCES the mode -----------------
def test_run_one_team_full_sends_overview_and_leaves_baseline(tmp_path):
    # team_full forces a FULL standup overview regardless of anchor/full. It is
    # READ-ONLY w.r.t. the delta baseline: it marks the per-day dedup but must
    # NEVER write the digest_team snapshot (that belongs to team_delta).
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    gl = _GitLab([_issue(1, gid=11, due=(dt.date.today() - dt.timedelta(days=1)).isoformat())])
    # anchor=False, full=None on purpose: the split adapter ignores both.
    r1 = cron.run_one(engine, gl, "g1", store, "team_full", room="!r", skey="s1")
    assert r1 == {"sent": 1, "reason": "ok", "recipients": ["room:!r"],
                  "html": "<digest_team>", "n_changes": 0}      # full -> 0 changes
    assert engine.handled[0].extra["mode"] == "full"
    assert store.already_sent("digest_team", "s1:group")
    assert store.get_state("digest_team", "s1:group") is None       # baseline NOT written
    r2 = cron.run_one(engine, gl, "g1", store, "team_full", room="!r", skey="s1")
    assert r2["sent"] == 0                                           # per-day dedup holds
    assert len(engine.handled) == 1


def test_run_one_team_delta_owns_the_baseline(tmp_path):
    # team_delta forces DELTA mode. With no baseline it learns one silently
    # (no_baseline); once seeded, a real change advances it on a committed send.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    yest = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    gl = _GitLab([_issue(1, gid=11, due=yest)])
    r = cron.run_one(engine, gl, "g1", store, "team_delta", room="!r", skey="s1")
    assert r["sent"] == 0 and r["reason"] == "no_baseline"
    assert engine.handled == []
    assert store.get_state("digest_team", "s1:group") is not None   # delta learned the baseline


# --- (e) the split-digests migration -------------------------------------
def _pass_row(store, name):
    return store.get_state("settings", f"pass:{name}")


def test_migration_splits_old_digest_and_team(tmp_path):
    # Live DB with the old anchor-dual pass rows: digest (anchor Wed/Fri, days
    # Mon..Fri, 09:00) + team (anchor Wed/Fri, 09:30). After migration: full pass
    # = anchor days, delta pass = the remaining days, time/enabled carried over.
    store = Store(path=str(tmp_path / "s.db"))
    store.set_state("settings", "pass:digest",
                    {"enabled": True, "days": [0, 1, 2, 3, 4], "time": "09:00", "anchor_days": [2, 4]})
    store.set_state("settings", "pass:team",
                    {"enabled": True, "days": [0, 1, 2, 3, 4], "time": "09:30", "anchor_days": [2, 4]})
    store.set_state("source", "s1", {"group_id": "g1", "enabled": True})

    today = dt.date(2026, 6, 5)                       # Friday
    assert settings.migrate_split_digests(store, today=today) is True

    assert _pass_row(store, "digest_full") == {"enabled": True, "days": [2, 4], "time": "09:00"}
    assert _pass_row(store, "digest_delta") == {"enabled": True, "days": [0, 1, 3], "time": "09:00"}
    assert _pass_row(store, "team_full") == {"enabled": True, "days": [2, 4], "time": "09:30"}
    assert _pass_row(store, "team_delta") == {"enabled": True, "days": [0, 1, 3], "time": "09:30"}
    # Old rows left in place (rollback escape hatch).
    assert _pass_row(store, "digest") is not None and _pass_row(store, "team") is not None
    # Run-history seeded (dated yesterday) for every new pass × source, so the
    # scheduler treats them as established and won't blast on first tick.
    yest = (today - dt.timedelta(days=1)).isoformat()
    for name in ("digest_full", "digest_delta", "team_full", "team_delta"):
        run = store.last_run(keys.ns("s1", name))
        assert run is not None and run["iso"] == yest and run["ok"] is True


def test_migration_is_idempotent_and_marker_guarded(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    store.set_state("settings", "pass:digest",
                    {"enabled": True, "days": [0, 1, 2, 3, 4], "time": "09:00", "anchor_days": [2, 4]})
    assert settings.migrate_split_digests(store, today=dt.date(2026, 6, 5)) is True
    assert store.get_state("migration", "split_digests_v1") is True   # marker set
    # An operator edit AFTER migration must not be clobbered by a re-run.
    store.set_state("settings", "pass:digest_full", {"enabled": False, "days": [1], "time": "07:00"})
    assert settings.migrate_split_digests(store, today=dt.date(2026, 6, 5)) is False   # no-op
    assert _pass_row(store, "digest_full") == {"enabled": False, "days": [1], "time": "07:00"}


def test_migration_defaults_when_no_old_rows(tmp_path):
    # Fresh DB (no old pass rows): the migration still creates the four passes
    # with their registry-default schedules, and seeds run-history under skey=""
    # when no sources are configured.
    store = Store(path=str(tmp_path / "s.db"))
    today = dt.date(2026, 6, 5)
    assert settings.migrate_split_digests(store, today=today) is True
    assert _pass_row(store, "digest_full") == {"enabled": True, "days": [2, 4], "time": "09:00"}
    assert _pass_row(store, "team_delta") == {"enabled": True, "days": [0, 1, 3], "time": "09:30"}
    yest = (today - dt.timedelta(days=1)).isoformat()
    assert store.last_run(keys.ns("", "digest_full"))["iso"] == yest
