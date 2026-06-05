"""Signed, expiring, rotatable admin session tokens + login throttle.

Dependency-free (stdlib only). The old scheme made the session cookie a static
HMAC of ADMIN_PASSWORD — identical for every login, never expiring, impossible
to revoke without changing the password. This replaces it with a proper signed
token:

  token = b64url(json(payload)) + "." + hex(hmac_sha256(server_secret, b64url))
  payload = {"v":1, "iat":<epoch>, "exp":<iat+TTL>, "nonce":<random>}

The signing secret is a per-deployment random value persisted in the state DB
(key ("settings","session_secret")). Rotating that row instantly invalidates
every outstanding session — a real logout/revoke, not a client-side cookie wipe.
The token carries its own expiry, so an old cookie stops working after the TTL
even if the secret is unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

# Token lifetime. A cookie older than this fails verification regardless of the
# secret, so sessions don't live forever.
SESSION_TTL = 7 * 24 * 3600  # 7 days, in seconds
_VERSION = 1

# State-store coordinates for the persisted signing secret.
_SECRET_KIND = "settings"
_SECRET_KEY = "session_secret"

# --- login throttle tuning (per-IP, in-process) --------------------------
THROTTLE_MAX_FAILS = 5     # failures allowed within the window before locking
THROTTLE_WINDOW = 60.0     # seconds: only failures this recent count
THROTTLE_COOLDOWN = 60.0   # seconds an IP stays locked once tripped


# --- server secret (persisted, rotatable) --------------------------------
def get_or_create_secret(store) -> str:
    """Read the per-deployment signing secret from the state DB, creating it on
    first use. Persisted so sessions survive restarts; rewrite/delete the row to
    rotate it and revoke every existing session."""
    secret = store.get_state(_SECRET_KIND, _SECRET_KEY)
    if not secret:
        secret = secrets.token_hex(32)
        store.set_state(_SECRET_KIND, _SECRET_KEY, secret)
    return secret


# --- token mint / verify -------------------------------------------------
def _b64url(raw: bytes) -> str:
    # URL-safe base64 without padding, so the value is cookie-safe.
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(secret: str, b64_payload: str) -> str:
    return hmac.new(secret.encode(), b64_payload.encode(), hashlib.sha256).hexdigest()


def mint_session(secret: str, *, now: int | None = None) -> str:
    """Create a fresh signed session token valid for SESSION_TTL seconds."""
    iat = int(now if now is not None else time.time())
    payload = {"v": _VERSION, "iat": iat, "exp": iat + SESSION_TTL,
               "nonce": secrets.token_hex(8)}
    b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{b64}.{_sign(secret, b64)}"


def verify_session(token: str | None, secret: str, *, now: int | None = None) -> bool:
    """True iff `token` is a well-formed, correctly-signed, unexpired token for
    `secret`. Exception-safe: any malformed input returns False, never raises."""
    if not token or not secret:
        return False
    try:
        b64, sig = token.split(".", 1)
        expected = _sign(secret, b64)
        # Constant-time compare so a wrong signature leaks no timing signal.
        if not hmac.compare_digest(sig, expected):
            return False
        padding = "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(b64 + padding))
        if payload.get("v") != _VERSION:
            return False
        clock = now if now is not None else time.time()
        return clock <= float(payload["exp"])
    except Exception:   # noqa: BLE001 — malformed token -> not authenticated
        return False


# --- per-IP login throttle (in-process, naive by design) -----------------
class LoginThrottle:
    """Naive in-memory per-IP lockout for the login endpoint. Process-local
    (resets on restart) and does not trust X-Forwarded-For — it just raises the
    cost of online brute-forcing the single shared password.

    A clock is injectable for tests; production passes none (uses time.time)."""

    def __init__(self, *, max_fails: int = THROTTLE_MAX_FAILS,
                 window: float = THROTTLE_WINDOW, cooldown: float = THROTTLE_COOLDOWN,
                 clock=time.time):
        self._max = max_fails
        self._window = window
        self._cooldown = cooldown
        self._clock = clock
        # ip -> {"fails": [timestamps], "locked_until": float}
        self._state: dict[str, dict] = {}

    def retry_after(self, ip: str) -> float:
        """Seconds remaining on the lock for `ip`, or 0 if not locked."""
        st = self._state.get(ip)
        if not st:
            return 0.0
        return max(0.0, st.get("locked_until", 0.0) - self._clock())

    def is_locked(self, ip: str) -> bool:
        return self.retry_after(ip) > 0

    def record_failure(self, ip: str) -> None:
        """Count a failed login; lock the IP once it trips the threshold."""
        now = self._clock()
        st = self._state.setdefault(ip, {"fails": [], "locked_until": 0.0})
        # Keep only failures within the sliding window.
        st["fails"] = [t for t in st["fails"] if now - t < self._window] + [now]
        if len(st["fails"]) >= self._max:
            st["locked_until"] = now + self._cooldown
            st["fails"] = []   # reset the window; the lock now governs access

    def reset(self, ip: str) -> None:
        """Clear all failure state for an IP (called on a successful login)."""
        self._state.pop(ip, None)
