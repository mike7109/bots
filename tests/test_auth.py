"""Admin session auth: signed/expiring tokens + the per-IP login throttle.

Imports auth.py directly — NOT app.py, which builds a Store on /data at import.
"""
from __future__ import annotations

import auth


# --- mint / verify round-trip -------------------------------------------
def test_mint_then_verify_roundtrip():
    secret = "s" * 64
    token = auth.mint_session(secret)
    assert auth.verify_session(token, secret) is True


def test_verify_rejects_tampered_token():
    secret = "s" * 64
    token = auth.mint_session(secret)
    b64, sig = token.split(".", 1)
    # Flip a char in the signature -> HMAC no longer matches.
    bad_sig = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert auth.verify_session(f"{b64}.{bad_sig}", secret) is False
    # Tamper the payload but keep the old signature -> mismatch.
    assert auth.verify_session(f"{b64}x.{sig}", secret) is False


def test_verify_rejects_wrong_secret():
    token = auth.mint_session("a" * 64)
    assert auth.verify_session(token, "b" * 64) is False


def test_verify_rejects_expired_token():
    secret = "s" * 64
    # Minted "in the past" so it's already expired by the time we check `now`.
    issued = 1_000_000
    token = auth.mint_session(secret, now=issued)
    assert auth.verify_session(token, secret, now=issued + 10) is True            # still fresh
    assert auth.verify_session(token, secret, now=issued + auth.SESSION_TTL - 1) is True
    assert auth.verify_session(token, secret, now=issued + auth.SESSION_TTL + 1) is False  # expired


def test_verify_rejects_wrong_version():
    import base64
    import hashlib
    import hmac
    import json
    secret = "s" * 64
    payload = {"v": 2, "iat": 0, "exp": 9_999_999_999, "nonce": "x"}
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = hmac.new(secret.encode(), b64.encode(), hashlib.sha256).hexdigest()
    assert auth.verify_session(f"{b64}.{sig}", secret) is False


def test_verify_malformed_and_empty_return_false_not_raise():
    secret = "s" * 64
    for bad in (None, "", "no-dot", "a.b.c.d", "....", "x.y", "notbase64!!!.deadbeef"):
        assert auth.verify_session(bad, secret) is False
    # Empty secret never verifies.
    assert auth.verify_session(auth.mint_session(secret), "") is False


def test_mint_produces_distinct_tokens():
    secret = "s" * 64
    assert auth.mint_session(secret) != auth.mint_session(secret)   # nonce differs


# --- persisted server secret --------------------------------------------
class _FakeStore:
    """Minimal get_state/set_state over a dict (no SQLite)."""
    def __init__(self):
        self._d = {}

    def get_state(self, kind, key):
        return self._d.get((kind, str(key)))

    def set_state(self, kind, key, value):
        self._d[(kind, str(key))] = value


def test_secret_created_once_and_persisted():
    store = _FakeStore()
    s1 = auth.get_or_create_secret(store)
    s2 = auth.get_or_create_secret(store)
    assert s1 == s2 and len(s1) == 64          # stable across calls, 32 bytes hex
    # Rotating (clearing) the row revokes outstanding sessions.
    token = auth.mint_session(s1)
    store._d.clear()
    s3 = auth.get_or_create_secret(store)
    assert s3 != s1
    assert auth.verify_session(token, s3) is False


# --- login throttle ------------------------------------------------------
class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_throttle_locks_after_n_failures_and_unlocks_after_cooldown():
    clk = _Clock()
    t = auth.LoginThrottle(max_fails=5, window=60.0, cooldown=60.0, clock=clk)
    ip = "10.0.0.1"
    # First 4 failures: not locked yet.
    for _ in range(4):
        t.record_failure(ip)
        assert t.is_locked(ip) is False
    # 5th failure trips the lock.
    t.record_failure(ip)
    assert t.is_locked(ip) is True
    assert t.retry_after(ip) == 60.0
    # Still locked just before cooldown elapses.
    clk.t = 59.0
    assert t.is_locked(ip) is True
    # Unlocks once the cooldown passes.
    clk.t = 61.0
    assert t.is_locked(ip) is False


def test_throttle_reset_clears_failures():
    clk = _Clock()
    t = auth.LoginThrottle(max_fails=5, clock=clk)
    ip = "10.0.0.2"
    for _ in range(4):
        t.record_failure(ip)
    t.reset(ip)                      # successful login wipes the slate
    for _ in range(4):
        t.record_failure(ip)
    assert t.is_locked(ip) is False  # counter restarted, only 4 since reset


def test_throttle_window_expires_old_failures():
    clk = _Clock()
    t = auth.LoginThrottle(max_fails=5, window=60.0, clock=clk)
    ip = "10.0.0.3"
    for _ in range(4):
        t.record_failure(ip)
    clk.t = 120.0                    # old failures fall out of the window
    t.record_failure(ip)            # only 1 failure inside the window now
    assert t.is_locked(ip) is False


def test_throttle_is_per_ip():
    clk = _Clock()
    t = auth.LoginThrottle(max_fails=5, clock=clk)
    for _ in range(5):
        t.record_failure("1.1.1.1")
    assert t.is_locked("1.1.1.1") is True
    assert t.is_locked("2.2.2.2") is False
