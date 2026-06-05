"""SendGuard: a generic rate-limit + circuit breaker for outbound sends.

This is a SAFETY mechanism, not a feature. If a bug makes the bot spam (a cron
pass looping, a webhook storm, a duplicated message firing in a tight loop), the
guard auto-detects the runaway volume, TRIPS the breaker — which STOPS all
subsequent sending — and calls `on_trip` so the operator gets alerted. The
breaker stays tripped until an operator resets it (or, optionally, after a
cooldown), so a crash-loop can't quietly resume the flood.

Where it lives: the notification Engine's single dispatch chokepoint. Every
send — webhook-driven and cron-pass-driven — flows through `Engine.handle`, so
recording each delivered message there means ANY runaway pass is caught
centrally. The guard records a send AFTER it goes out, so a trip stops the NEXT
send: the one message that crossed the threshold has already left, but the point
is to stop the flood that follows, not to be perfectly off-by-one.

Three independent caps, evaluated on every `record()`; tripping on ANY:

  GLOBAL      total sends in the last `window_s` seconds        > max_global
  PER-TARGET  sends to one target_key in `target_window_s`      > max_per_target
  DUPLICATE   identical content (by hash) in `window_s`         > max_duplicate

State that must survive a restart (the tripped flag + when/why) is read/written
through injected callables (`get_tripped`/`set_tripped`) so the persistence layer
stays out of botkit. The rolling window of recent sends is in-memory only — a
restart loses it, which is fine: the persistent tripped flag is what actually
keeps a crash-loop stopped.

Design rules:
  * Exception-safe throughout. The guard must NEVER break delivery by raising —
    every public method swallows its own errors (recording/evaluation failures
    fail OPEN for `record`, and `blocked()` fails OPEN i.e. returns False, so a
    guard bug can't wedge the bot into permanent silence).
  * Disabled (`enabled=False`) is cheap and inert: `blocked()` is always False,
    `record()` never trips.
  * Dependency-free (stdlib only) and `clock`-injectable for tests.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import deque

log = logging.getLogger("botkit.notify.guard")


class SendGuard:
    def __init__(self, *, enabled, window_s, max_global, target_window_s,
                 max_per_target, max_duplicate, auto_reset_s,
                 get_tripped, set_tripped, on_trip, clock=time.time):
        self.enabled = bool(enabled)
        self.window_s = int(window_s)
        self.max_global = int(max_global)
        self.target_window_s = int(target_window_s)
        self.max_per_target = int(max_per_target)
        self.max_duplicate = int(max_duplicate)
        self.auto_reset_s = int(auto_reset_s)
        self._get_tripped = get_tripped
        self._set_tripped = set_tripped
        self._on_trip = on_trip
        self._clock = clock
        # Rolling window of recent sends: each entry (ts, target_key, content_hash).
        self._events: deque = deque()
        # Longest lookback we ever need to retain entries for.
        self._max_window = max(self.window_s, self.target_window_s)

    # --- internal helpers ------------------------------------------------
    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:16]

    def _prune(self, now: float) -> None:
        """Drop entries older than the longest window (cheap; from the left)."""
        cutoff = now - self._max_window
        ev = self._events
        while ev and ev[0][0] < cutoff:
            ev.popleft()

    # --- breaker state ---------------------------------------------------
    def blocked(self) -> bool:
        """True iff sending is currently stopped (breaker tripped).

        Reads the persistent tripped flag. If `auto_reset_s > 0` and enough time
        has elapsed since the trip, auto-resets and returns False. Fails OPEN
        (returns False) on any error so a guard fault can't permanently silence
        the bot."""
        if not self.enabled:
            return False
        try:
            state = self._get_tripped() or {}
            if not state.get("tripped"):
                return False
            if self.auto_reset_s > 0:
                since = self._parse_since(state.get("since"))
                if since is not None and (self._clock() - since) >= self.auto_reset_s:
                    self.reset()
                    return False
            return True
        except Exception:                       # noqa: BLE001 — never let the guard wedge delivery
            log.exception("SendGuard.blocked() failed; failing open")
            return False

    def _parse_since(self, since) -> float | None:
        """`since` is stored as the clock value at trip time (a float seconds).
        Returns it as a float, or None if unparseable (then no auto-reset)."""
        if since is None:
            return None
        try:
            return float(since)
        except (TypeError, ValueError):
            return None

    def trip(self, reason: str) -> None:
        """Persist the tripped state and fire `on_trip` exactly once. Idempotent:
        a second call while already tripped is a no-op (so `on_trip` — which
        alerts the operator — fires once per trip, not once per over-limit send).
        Best-effort: never raises."""
        try:
            state = self._get_tripped() or {}
            if state.get("tripped"):
                return                          # already tripped -> idempotent
            now = self._clock()
            self._set_tripped({"tripped": True, "since": now, "reason": reason})
        except Exception:                       # noqa: BLE001
            log.exception("SendGuard.trip() persist failed")
            # Still attempt the alert below — operator should hear about it.
        try:
            self._on_trip(reason)
        except Exception:                       # noqa: BLE001 — alert is best-effort
            log.exception("SendGuard on_trip callback failed")

    def reset(self) -> None:
        """Clear the breaker (operator action / auto-reset) and the in-memory
        window. Best-effort: never raises."""
        try:
            self._set_tripped({"tripped": False})
        except Exception:                       # noqa: BLE001
            log.exception("SendGuard.reset() persist failed")
        self._events.clear()

    # --- recording -------------------------------------------------------
    def record(self, target_key: str, content: str) -> None:
        """Note a send that just happened, then evaluate the three caps and trip
        if ANY is exceeded. Never raises (a guard fault must not break delivery).
        Recording AFTER the send means a trip stops the NEXT send, not this one."""
        if not self.enabled:
            return
        try:
            now = self._clock()
            self._prune(now)
            chash = self._hash(content)
            self._events.append((now, str(target_key), chash))

            # GLOBAL: total sends in the last window_s.
            g_cut = now - self.window_s
            global_n = sum(1 for ts, _, _ in self._events if ts >= g_cut)
            if global_n > self.max_global:
                self.trip(f"глобальный лимит: {global_n} отправок за {self.window_s}с "
                          f"(порог {self.max_global})")
                return

            # PER-TARGET: sends to this target_key in the last target_window_s.
            t_cut = now - self.target_window_s
            target_n = sum(1 for ts, tk, _ in self._events
                           if tk == str(target_key) and ts >= t_cut)
            if target_n > self.max_per_target:
                self.trip(f"лимит на адресата «{target_key}»: {target_n} за "
                          f"{self.target_window_s}с (порог {self.max_per_target})")
                return

            # DUPLICATE: identical content in the last window_s.
            dup_n = sum(1 for ts, _, ch in self._events if ch == chash and ts >= g_cut)
            if dup_n > self.max_duplicate:
                self.trip(f"дубликаты: одно и то же сообщение {dup_n} раз за "
                          f"{self.window_s}с (порог {self.max_duplicate})")
                return
        except Exception:                       # noqa: BLE001 — recording must never break delivery
            log.exception("SendGuard.record() failed; ignoring")
