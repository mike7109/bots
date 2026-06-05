"""keys.ns: source-namespacing of dedup/state keys (promoted from digests._ns).

A non-empty source key prefixes `skey:`; an empty/falsy source key leaves the
key bare so single-source deployments keep their old, un-prefixed ledger keys.
The value is always stringified.
"""
from __future__ import annotations

import keys


def test_ns_with_source_prefixes():
    assert keys.ns("g1", "x") == "g1:x"


def test_ns_empty_source_is_bare():
    assert keys.ns("", "x") == "x"


def test_ns_none_source_stringifies_key():
    assert keys.ns(None, 5) == "5"


def test_ns_stringifies_int_key_when_namespaced():
    assert keys.ns("s1", 42) == "s1:42"


def test_kind_constants_match_ledger_literals():
    # The named constants must equal the exact strings persisted in the ledger,
    # or promoting them would silently re-namespace existing dedup history.
    assert keys.DUE_SOON == "due_soon"
    assert keys.OVERDUE == "overdue"
    assert keys.DIGEST_PERSONAL == "digest_personal"
    assert keys.DIGEST_TEAM == "digest_team"
    assert keys.TRIAGE == "triage"
    assert keys.STALE == "stale"
    assert keys.METRICS == "metrics"
    assert keys.SCHED == "sched"
    assert keys.RUN == "run"
    assert keys.ALERT == "alert"
