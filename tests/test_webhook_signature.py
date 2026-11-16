"""ElevenLabs post-call webhook signatures: `ElevenLabs-Signature: t=<unix secs>,v0=<hex>`.

v0 is HMAC-SHA256(secret, f"{t}.{raw body}"), the scheme the official SDK's
webhooks.construct_event checks. Timestamps older than 30 minutes are rejected.
"""

import hashlib
import hmac

import pytest

from bear.webhook_signature import SignatureError, verify_signature

SECRET = "wsec_fixture_secret"
T = 1739537297
BODY = b'{"type":"post_call_transcription","data":{}}'
# Computed with the SDK's algorithm; pins the exact message format ("<t>.<body>").
KNOWN_V0 = "v0=cff499f69bec612b8c1b89d6bd908eb16a973643f6438182fa644979c4be5c04"


def sign(body, timestamp, secret=SECRET):
    return "v0=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def test_known_vector_verifies():
    verify_signature(BODY, f"t={T},{KNOWN_V0}", SECRET, now=T + 5)


def test_fresh_signature_verifies():
    verify_signature(BODY, f"t={T},{sign(BODY, T)}", SECRET, now=T + 29 * 60)


def test_any_matching_v0_is_accepted_during_secret_rotation():
    header = f"t={T},v0={'0' * 64},{sign(BODY, T)}"
    verify_signature(BODY, header, SECRET, now=T)


@pytest.mark.parametrize(
    "header,now,reason",
    [
        (None, T, "missing"),
        ("", T, "missing"),
        (f"{KNOWN_V0}", T, "malformed"),
        (f"t={T}", T, "malformed"),
        (f"t=abc,{KNOWN_V0}", T, "malformed"),
        (f"t={T},v1=abc", T, "malformed"),
        (f"t={T},v0={'0' * 64}", T, "mismatch"),
        (f"t={T + 1},{KNOWN_V0}", T, "mismatch"),
        (f"t={T},{KNOWN_V0}", T + 30 * 60 + 1, "expired"),
        (f"t={T},{KNOWN_V0}", T - 5 * 60 - 1, "future"),
    ],
)
def test_bad_signatures_are_rejected(header, now, reason):
    with pytest.raises(SignatureError) as error:
        verify_signature(BODY, header, SECRET, now=now)
    assert error.value.reason == reason


def test_tampered_body_or_wrong_secret_is_rejected():
    header = f"t={T},{sign(BODY, T)}"
    with pytest.raises(SignatureError, match="mismatch"):
        verify_signature(BODY + b" ", header, SECRET, now=T)
    with pytest.raises(SignatureError, match="mismatch"):
        verify_signature(BODY, header, "wsec_other_secret", now=T)


def test_empty_secret_never_verifies():
    with pytest.raises(SignatureError, match="no secret"):
        verify_signature(BODY, f"t={T},{sign(BODY, T, '')}", "", now=T)
