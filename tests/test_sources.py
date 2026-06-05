"""Sources.match_path: route a webhook to a source. Longest full_path prefix
wins; a single configured source is the catch-all fallback."""
from __future__ import annotations

import pytest

from botkit.store import Store
from settings import Settings
from sources import Sources


@pytest.fixture
def store(tmp_path):
    return Store(path=str(tmp_path / "state.db"))


@pytest.fixture
def sources(store):
    return Sources(store, Settings(store))


def _add(sources, sid, full_path, *, enabled=True, room=None):
    sources.upsert({"id": sid, "name": sid, "group_id": sid,
                    "full_path": full_path, "enabled": enabled,
                    "room": room or f"!{sid}"})


# --- match_path ----------------------------------------------------------
def test_match_path_exact_prefix(sources):
    _add(sources, "a", "fakspro")
    assert sources.match_path("fakspro/infra")["id"] == "a"


def test_match_path_exact_equality(sources):
    _add(sources, "a", "fakspro/infra")
    assert sources.match_path("fakspro/infra")["id"] == "a"


def test_match_path_longest_prefix_wins(sources):
    _add(sources, "broad", "fakspro")
    _add(sources, "narrow", "fakspro/infra")
    # both prefixes match; the longer (more specific) one wins
    assert sources.match_path("fakspro/infra/repo")["id"] == "narrow"


def test_match_path_no_partial_segment_match(sources):
    _add(sources, "a", "fakspro/infra")
    _add(sources, "b", "other")     # 2nd source so single-source fallback won't fire
    # "fakspro/infrastructure" must NOT match "fakspro/infra" (segment boundary)
    assert sources.match_path("fakspro/infrastructure") is None


def test_match_path_single_source_fallback(sources):
    _add(sources, "only", "")                # no full_path
    # single enabled source -> everything routes to it
    assert sources.match_path("anything/at/all")["id"] == "only"


def test_match_path_no_fallback_when_multiple_and_no_match(sources):
    _add(sources, "a", "groupa")
    _add(sources, "b", "groupb")
    assert sources.match_path("groupc/x") is None    # ambiguous -> no route


def test_match_path_ignores_disabled_sources(sources):
    _add(sources, "a", "fakspro", enabled=False)
    _add(sources, "b", "other")
    # disabled 'a' wouldn't match anyway; with one enabled source left, fallback applies
    assert sources.match_path("fakspro/infra")["id"] == "b"


def test_match_path_empty_path(sources):
    _add(sources, "a", "fakspro")
    _add(sources, "b", "other")     # 2nd source so single-source fallback won't fire
    # empty / None path matches no prefix and has no single-source fallback
    assert sources.match_path("") is None
    assert sources.match_path(None) is None


def test_match_path_empty_path_single_source_still_falls_back(sources):
    # NOTE: current behavior — with exactly one enabled source, even an empty
    # path routes to it (the single-source catch-all fires before giving up).
    _add(sources, "only", "fakspro")
    assert sources.match_path("")["id"] == "only"


# --- all / enabled / upsert ----------------------------------------------
def test_enabled_filters_disabled(sources):
    _add(sources, "a", "x", enabled=True)
    _add(sources, "b", "y", enabled=False)
    assert {s["id"] for s in sources.enabled()} == {"a"}
    assert {s["id"] for s in sources.all()} == {"a", "b"}


def test_upsert_defaults_enabled(sources):
    sources.upsert({"id": "z", "group_id": "1"})
    assert sources.get("z")["enabled"] is True
