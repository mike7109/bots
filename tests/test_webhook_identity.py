"""webhook.verify_token (constant-time, unset secret rejects all) and
Identity.matrix_pill (HTML-escapes a malicious display name / mxid)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from markupsafe import Markup

from botkit.identity import Identity
from botkit.webhook import verify_token


# --- verify_token --------------------------------------------------------
def test_verify_token_exact_match_passes():
    verify_token("s3cret", "s3cret")        # no exception == pass


def test_verify_token_mismatch_raises_403():
    with pytest.raises(HTTPException) as exc:
        verify_token("wrong", "s3cret")
    assert exc.value.status_code == 403


def test_verify_token_unset_secret_rejects_all():
    # An unset secret must reject everything, including an empty token.
    with pytest.raises(HTTPException) as exc:
        verify_token("anything", "")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        verify_token("", "")


def test_verify_token_none_received_rejected():
    with pytest.raises(HTTPException):
        verify_token(None, "s3cret")


# --- Identity ------------------------------------------------------------
def test_matrix_id_explicit_then_domain_fallback():
    ident = Identity({"bob": {"matrix": "@bob:srv"}}, matrix_domain="fakspro.ru")
    assert ident.matrix_id("bob") == "@bob:srv"           # explicit wins
    assert ident.matrix_id("carol") == "@carol:fakspro.ru"  # domain fallback


def test_matrix_id_no_domain_no_user_is_none():
    ident = Identity({})
    assert ident.matrix_id("nobody") is None
    assert ident.matrix_id(None) is None


def test_display_name_falls_back_to_login():
    ident = Identity({"bob": {"name": "Bob B"}})
    assert ident.display_name("bob") == "Bob B"
    assert ident.display_name("anon") == "anon"
    assert ident.display_name(None) == ""


def test_matrix_pill_empty_login():
    assert Identity({}).matrix_pill(None) == Markup("")


def test_matrix_pill_no_mxid_returns_escaped_name():
    ident = Identity({})         # no matrix id available
    pill = ident.matrix_pill("<b>x</b>")
    assert "<b>" not in pill     # name is escaped
    assert "&lt;b&gt;" in pill
    assert "<a href" not in pill


def test_matrix_pill_escapes_malicious_display_name():
    evil = '"><script>alert(1)</script>'
    ident = Identity({"bob": {"matrix": "@bob:srv", "name": evil}})
    pill = ident.matrix_pill("bob")
    assert "<script>" not in pill                  # the raw payload never survives
    assert "&lt;script&gt;" in pill
    assert 'href="https://matrix.to/#/@bob:srv"' in pill


def test_matrix_pill_escapes_malicious_mxid():
    # A crafted mxid (from config) must also be escaped inside the href.
    ident = Identity({"bob": {"matrix": '@bob:srv"><script>'}})
    pill = ident.matrix_pill("bob")
    assert "<script>" not in pill
    assert "&gt;" in pill or "&#34;" in pill
