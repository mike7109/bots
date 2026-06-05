"""Named kinds + the source-namespacing helper for the dedup/state ledger.

Every dedup/snapshot/run record in botkit.store is keyed by a `kind` (the
namespace dimension) plus a key. Those kinds used to be bare string literals
scattered across digests/cron/scheduler/admin — a typo (`"due_son"`) would
silently open a fresh, never-deduped namespace instead of failing loudly.
Centralising them here makes the set discoverable and a typo a NameError.

`ns()` namespaces a key by source so two GitLab groups sharing the same issue
id / login don't collide in the ledger.
"""
from __future__ import annotations

# --- pass / ledger kinds -------------------------------------------------
# Per-issue reminders (cron.py).
DUE_SOON = "due_soon"
OVERDUE = "overdue"

# Digest passes (digests.py).
DIGEST_PERSONAL = "digest_personal"
DIGEST_TEAM = "digest_team"
TRIAGE = "triage"
STALE = "stale"
METRICS = "metrics"

# Scheduling / ops ledgers (cron.py, scheduler.py, alerts.py).
SCHED = "sched"          # per-source×pass "already fired today" guard
RUN = "run"              # per-pass last-run history (store.record_run)
ALERT = "alert"          # operator-alert throttle (alerts.py)


def ns(skey: str, key) -> str:
    """Namespace a dedup/snapshot key by source so groups don't collide."""
    return f"{skey}:{key}" if skey else str(key)
