"""FastAPI-agnostic webhook helpers."""
from __future__ import annotations

import hmac

from fastapi import HTTPException


def verify_token(received: str, secret: str) -> None:
    """Constant-time check of GitLab's `X-Gitlab-Token` header. Raises 403."""
    if not hmac.compare_digest(received or "", secret or ""):
        raise HTTPException(status_code=403, detail="bad webhook token")
