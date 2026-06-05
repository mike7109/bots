"""FastAPI-agnostic webhook helpers."""
from __future__ import annotations

import hmac

from fastapi import HTTPException


def verify_token(received: str, secret: str) -> None:
    """Constant-time check of GitLab's `X-Gitlab-Token` header. Raises 403.

    An unset secret rejects everything (don't leave the webhook open when the
    bot isn't configured yet)."""
    if not secret or not hmac.compare_digest(received or "", secret):
        raise HTTPException(status_code=403, detail="bad webhook token")
