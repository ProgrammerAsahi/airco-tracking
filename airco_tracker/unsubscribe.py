from __future__ import annotations

import base64
import hashlib
import hmac
import uuid


_TOKEN_VERSION = "v1"
_TOKEN_PURPOSE = "alerts-unsubscribe"


def sign_unsubscribe_token(secret: str, user_id: str, token_version: int) -> str:
    """Return an opaque, PII-free capability that can only pause alert mail.

    The matching web endpoint validates the same payload. Rotating a user's
    token version invalidates links issued before an email or preference
    change without storing bearer tokens in either repository or Azure Table.
    """
    normalized_user_id = str(uuid.UUID(user_id))
    if token_version < 1:
        raise ValueError("Unsubscribe token version must be positive")
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("EMAIL_UNSUBSCRIBE_SIGNING_KEY must contain at least 32 bytes")
    payload = (
        f"{_TOKEN_VERSION}\n{_TOKEN_PURPOSE}\n{normalized_user_id}\n{token_version}"
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_base64url(payload)}.{_base64url(signature)}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
