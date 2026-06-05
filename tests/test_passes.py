"""passes: the single-declaration PASS REGISTRY is the source of truth.

These pin that the registry (a) covers exactly today's scheduled passes plus the
issue webhook, (b) reproduces the previously-hardcoded PASS_DEFAULTS/HAS_ANCHOR
byte-for-byte, (c) exposes a uniform-signature, callable `run` adapter for each
scheduled pass, and (d) dispatches identically to the old run_one if-chain for a
representative pass (team) — so the registry refactor changed no behaviour.
"""
from __future__ import annotations

import datetime as dt

import cron
import passes
import settings
from botkit.store import Store

# The values the backend used to hardcode (settings.PASS_DEFAULTS / HAS_ANCHOR
# and cron.PASSES / DAILY). Pinned here so a registry edit that drifts from the
# historical defaults fails loudly.
WORKDAYS = [0, 1, 2, 3, 4]
EXPECTED_PASS_DEFAULTS = {
    "due":     {"enabled": True,  "days": WORKDAYS, "time": "09:00"},
    "overdue": {"enabled": True,  "days": WORKDAYS, "time": "09:00"},
    "digest":  {"enabled": True,  "days": WORKDAYS, "time": "09:00", "anchor_days": [2, 4]},
    "team":    {"enabled": True,  "days": WORKDAYS, "time": "09:30", "anchor_days": [2, 4]},
    "triage":  {"enabled": True,  "days": [0],      "time": "10:00"},
    "stale":   {"enabled": True,  "days": [0],      "time": "10:00", "days_idle": 14},
    "metrics": {"enabled": False, "days": [0],      "time": "10:00"},
}
EXPECTED_PASSES = ("due", "overdue", "digest", "team", "triage", "stale", "metrics")
EXPECTED_DAILY = ("due", "overdue", "digest", "team", "triage", "stale")
EXPECTED_HAS_ANCHOR = ("digest", "team")


# --- (a) coverage --------------------------------------------------------
def test_registry_covers_scheduled_passes_plus_issue_webhook():
    # Registry order is preserved; scheduled subset == old cron.PASSES; the one
    # webhook entry is "issue" (no schedule).
    assert list(passes.REGISTRY) == [*EXPECTED_PASSES, "issue"]
    assert passes.scheduled() == EXPECTED_PASSES == cron.PASSES
    assert passes.daily() == EXPECTED_DAILY == cron.DAILY
    issue = passes.REGISTRY["issue"]
    assert issue.trigger == "webhook"
    assert issue.run is None
    assert issue.schedule_defaults == {}
    assert "issue" not in passes.scheduled()


# --- (b) derived defaults equal the old hardcoded values -----------------
def test_pass_defaults_derived_from_registry_match_old():
    assert passes.pass_defaults() == EXPECTED_PASS_DEFAULTS
    assert settings.PASS_DEFAULTS == EXPECTED_PASS_DEFAULTS


def test_has_anchor_derived_from_registry_match_old():
    assert passes.has_anchor() == EXPECTED_HAS_ANCHOR
    assert settings.HAS_ANCHOR == EXPECTED_HAS_ANCHOR
    # mode=="auto" (anchor-dual) is exactly the two delta digests.
    assert tuple(p.name for p in passes.REGISTRY.values() if p.mode == "auto") == EXPECTED_HAS_ANCHOR


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
        assert set(result) == {"sent", "reason", "recipients", "html"}, name
        store.close()


# --- (d) dispatch-equivalence: run_one(team) == the old behaviour ---------
def test_run_one_team_anchor_dispatches_like_before(tmp_path):
    # On an anchor day (anchor=True) team posts a FULL standup overview once and
    # writes the dedup + baseline; a second run the same day is a no-op. This is
    # the exact shape/behaviour the pre-refactor if-chain produced.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    gl = _GitLab([_issue(1, gid=11, due=(dt.date.today() - dt.timedelta(days=1)).isoformat())])
    r1 = cron.run_one(engine, gl, "g1", store, "team", anchor=True, room="!r", skey="s1")
    assert r1 == {"sent": 1, "reason": "ok", "recipients": ["room:!r"], "html": "<digest_team>"}
    assert store.already_sent("digest_team", "s1:group")
    assert store.get_state("digest_team", "s1:group") is not None
    r2 = cron.run_one(engine, gl, "g1", store, "team", anchor=True, room="!r", skey="s1")
    assert r2["sent"] == 0
    assert len(engine.handled) == 1


def test_run_one_team_non_anchor_delta_no_baseline(tmp_path):
    # Off an anchor day with no baseline (anchor=False, full=None), team learns
    # the baseline silently and reports no_baseline — the old delta behaviour.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    gl = _GitLab([_issue(1, gid=11, due=(dt.date.today() - dt.timedelta(days=1)).isoformat())])
    r = cron.run_one(engine, gl, "g1", store, "team", anchor=False, room="!r", skey="s1")
    assert r["sent"] == 0 and r["reason"] == "no_baseline"
    assert engine.handled == []
    assert store.get_state("digest_team", "s1:group") is not None   # baseline learned
