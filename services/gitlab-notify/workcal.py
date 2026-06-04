"""Russian working calendar via isdayoff.ru — so holidays don't have to be
typed by hand. Free, no key: GET https://isdayoff.ru/YYYYMMDD -> "0" working,
"1" day off, "2" shortened pre-holiday (we treat as working), "4" error.

Results are cached per date in the state DB (one network call per day), and any
network failure degrades to "working" so a flaky calendar never blocks sends.
"""
from __future__ import annotations

import logging
import urllib.request

log = logging.getLogger("gitlab-notify.workcal")
_KIND = "isdayoff"


def is_day_off(date_iso: str, store=None) -> bool | None:
    """True if `date_iso` (YYYY-MM-DD) is a non-working day in RU, else False.
    None if it couldn't be determined (network error) — caller treats as working."""
    if store is not None:
        cached = store.get_state(_KIND, date_iso)
        if cached is not None:
            return cached.get("off")
    try:
        ymd = date_iso.replace("-", "")
        with urllib.request.urlopen(f"https://isdayoff.ru/{ymd}", timeout=4) as r:
            body = r.read().decode().strip()
    except Exception as e:                       # noqa: BLE001 — never block on the calendar
        log.warning("isdayoff lookup failed for %s: %s", date_iso, e)
        return None
    if body not in ("0", "1", "2"):
        return None
    off = body == "1"
    if store is not None:
        store.set_state(_KIND, date_iso, {"off": off})
    return off
