"""Verify the ElevenLabs post-call webhook signature without the ElevenLabs SDK.

Header: ``ElevenLabs-Signature: t=<unix seconds>,v0=<hex HMAC-SHA256 of "<t>.<raw body>">``.
This matches the SDK's ``webhooks.construct_event``, with two stricter checks: a constant-time
compare, and rejecting timestamps from more than 5 minutes in the future.
"""

import hashlib
import hmac
import re

MAX_AGE_SECS = 30 * 60  # the SDK's tolerance
MAX_SKEW_SECS = 5 * 60


class SignatureError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def verify_signature(raw: bytes, header: str | None, secret: str, *, now: float) -> None:
    """Raise SignatureError unless ``header`` is a fresh, valid signature of ``raw`` under ``secret``."""
    if not secret:
        raise SignatureError("no secret")
    if not header:
        raise SignatureError("missing")
    timestamp, candidates = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and re.fullmatch(r"\d{1,12}", value):
            timestamp = int(value)
        elif key == "v0" and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        raise SignatureError("malformed")
    if timestamp < now - MAX_AGE_SECS:
        raise SignatureError("expired")
    if timestamp > now + MAX_SKEW_SECS:
        raise SignatureError("future")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected.encode(), candidate.encode()) for candidate in candidates):
        raise SignatureError("mismatch")
