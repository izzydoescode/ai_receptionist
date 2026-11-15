"""ElevenLabs post-call webhook: signed, idempotent, and stores only the call summary."""

import copy
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from bear.db import audit_events, call_summaries
from bear.post_call import parse_transcription
from conftest import INIT_SECRET, POST_CALL_SECRET, SECRET, rows

URL = "/elevenlabs/post-call"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "elevenlabs"
WEST = {"shop_id": "west-shop", "name": "West Demo Shop", "city": "Doral"}


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))


def signed(raw, secret=POST_CALL_SECRET, timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return {"ElevenLabs-Signature": f"t={timestamp},v0={digest}", "Content-Type": "application/json"}


def post(client, event, **sign):
    raw = event if isinstance(event, bytes) else json.dumps(event).encode()
    return client.post(URL, content=raw, headers=signed(raw, **sign))


def with_shop(event, shop_id):
    event = copy.deepcopy(event)
    event["data"]["conversation_initiation_client_data"]["dynamic_variables"]["shop_id"] = shop_id
    return event


@pytest.fixture
def post_client(make_client):
    return make_client(_seed=False, _shops=[{}, WEST])


def all_summaries(client):
    with client.app.state.store.engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(call_summaries)).mappings()]


# ---------- parsing (pure) ----------


def test_parse_docs_shaped_transcription_keeps_only_summary_fields():
    summary = parse_transcription(fixture("post_call_transcription")["data"])
    assert summary.shop_id == "vice-city-auto"
    assert summary.fields == {
        "conversation_id": "conv_demo0001",
        "started_at": datetime.fromtimestamp(1739537297, UTC),
        "duration_secs": 95,
        "outcome": "success",
        "summary": "Demo Driver called about squeaking brakes and booked a brake inspection for Monday at 10 AM.",
        "title": "Brake inspection booked",
        "language": "en",
    }


def test_parse_tolerates_missing_and_odd_fields():
    data = {"conversation_id": "conv_bare"}
    summary = parse_transcription(data)
    assert summary.shop_id is None
    assert summary.fields == {
        "conversation_id": "conv_bare",
        "started_at": None,
        "duration_secs": None,
        "outcome": "unknown",
        "summary": "",
        "title": None,
        "language": None,
    }
    odd = {
        "conversation_id": "conv_odd",
        "metadata": {"start_time_unix_secs": True, "call_duration_secs": -3, "main_language": "not a language!"},
        "analysis": {"call_successful": "maybe", "transcript_summary": 42, "call_summary_title": ["x"]},
        "conversation_initiation_client_data": {"dynamic_variables": {"shop_id": {"nested": 1}}},
    }
    summary = parse_transcription(odd)
    assert summary.shop_id is None
    assert summary.fields["started_at"] is None
    assert summary.fields["duration_secs"] is None
    assert summary.fields["outcome"] == "unknown"
    assert summary.fields["summary"] == ""
    assert summary.fields["title"] is None
    assert summary.fields["language"] is None


def test_parse_falls_back_to_the_override_language_and_trims_long_text():
    data = fixture("post_call_transcription")["data"]
    del data["metadata"]["main_language"]
    data["conversation_initiation_client_data"]["conversation_config_override"]["agent"]["language"] = "es"
    data["analysis"]["transcript_summary"] = "  " + "a" * 5000 + "  "
    data["analysis"]["call_summary_title"] = "t" * 500
    fields = parse_transcription(data).fields
    assert fields["language"] == "es"
    assert fields["summary"] == "a" * 2000
    assert fields["title"] == "t" * 200


@pytest.mark.parametrize("conversation_id", [None, "", 7, "has space", "x" * 65])
def test_parse_rejects_a_bad_conversation_id(conversation_id):
    with pytest.raises(ValueError):
        parse_transcription({"conversation_id": conversation_id})


# ---------- endpoint ----------


def test_signed_transcription_is_stored_for_its_shop(post_client):
    response = post(post_client, fixture("post_call_transcription"))
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "stored"}
    [row] = rows(post_client, "call_summaries")
    assert row["conversation_id"] == "conv_demo0001"
    assert row["outcome"] == "success"
    assert row["duration_secs"] == 95
    with post_client.app.state.store.engine.connect() as conn:
        events = conn.execute(select(audit_events.c.shop_id, audit_events.c.event)).all()
    assert [tuple(e) for e in events] == [("vice-city-auto", "call.summarized")]


def test_transcript_audio_and_phone_numbers_are_never_stored(post_client):
    post(post_client, fixture("post_call_transcription"))
    stored = json.dumps(all_summaries(post_client), default=str)
    for secret_ish in ("3055550199", "5550110", "squeaking, can I", "Thanks for calling", "CA000000"):
        assert secret_ish not in stored


def test_retries_are_idempotent(post_client):
    event = fixture("post_call_transcription")
    assert post(post_client, event).json() == {"status": "stored"}
    again = post(post_client, event)
    assert again.status_code == 200
    assert again.json() == {"status": "duplicate"}
    assert len(all_summaries(post_client)) == 1


def test_other_shops_and_unknown_shops_are_filed_separately(post_client):
    event = fixture("post_call_transcription")
    post(post_client, with_shop(event, "west-shop"))
    other = with_shop(event, "no-such-shop")
    other["data"]["conversation_id"] = "conv_demo0002"
    post(post_client, other)
    by_conversation = {r["conversation_id"]: r["shop_id"] for r in all_summaries(post_client)}
    assert by_conversation == {"conv_demo0001": "west-shop", "conv_demo0002": "unknown"}


@pytest.mark.parametrize("name", ["post_call_audio", "call_initiation_failure"])
def test_other_event_types_are_acknowledged_and_ignored(post_client, name):
    response = post(post_client, fixture(name))
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert all_summaries(post_client) == []


def test_unknown_future_event_type_is_ignored(post_client):
    assert post(post_client, {"type": "something_new", "data": {}}).json() == {"status": "ignored"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"ElevenLabs-Signature": "garbage"},
        {"ElevenLabs-Signature": "t=1,v0=" + "0" * 64},
    ],
)
def test_unsigned_or_badly_signed_requests_are_rejected(post_client, headers):
    response = post_client.post(URL, content=json.dumps(fixture("post_call_transcription")).encode(), headers=headers)
    assert response.status_code == 401
    assert all_summaries(post_client) == []


@pytest.mark.parametrize("secret", [SECRET, INIT_SECRET, "wsec_wrong-secret"])
def test_other_secrets_do_not_sign_post_call_events(post_client, secret):
    response = post(post_client, fixture("post_call_transcription"), secret=secret)
    assert response.status_code == 401


def test_stale_signatures_are_rejected(post_client):
    response = post(post_client, fixture("post_call_transcription"), timestamp=int(time.time()) - 31 * 60)
    assert response.status_code == 401


def test_signature_is_checked_before_the_body_is_parsed(post_client):
    response = post_client.post(URL, content=b"not json", headers={"ElevenLabs-Signature": "t=1,v0=00"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"[]", b'{"type": 1, "data": {}}', b'{"type": "post_call_transcription"}'],
)
def test_signed_malformed_bodies_are_rejected(post_client, raw):
    assert post(post_client, raw).status_code == 400


def test_signed_transcription_without_a_valid_conversation_id_is_rejected(post_client):
    event = fixture("post_call_transcription")
    event["data"]["conversation_id"] = "bad id"
    assert post(post_client, event).status_code == 400


def test_oversized_bodies_are_rejected_before_verifying(post_client):
    raw = b'{"type":"post_call_audio","data":{"full_audio":"' + b"A" * (2 * 1024 * 1024) + b'"}}'
    assert post(post_client, raw).status_code == 413


def test_endpoint_is_disabled_without_a_secret(make_client):
    client = make_client(_seed=False, POST_CALL_WEBHOOK_SECRET=None)
    response = post(client, fixture("post_call_transcription"), secret="")
    assert response.status_code == 503


@pytest.mark.parametrize("reused", [SECRET, INIT_SECRET])
def test_post_call_secret_must_differ_from_the_other_secrets(make_client, reused):
    with pytest.raises(RuntimeError, match="POST_CALL_WEBHOOK_SECRET must differ"):
        make_client(_seed=False, POST_CALL_WEBHOOK_SECRET=reused)


def test_logs_carry_ids_but_never_the_summary(post_client, caplog):
    with caplog.at_level(logging.INFO, logger="bear"):
        post(post_client, fixture("post_call_transcription"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert '"conversation_id":"conv_demo0001"' in text
    assert '"shop_id":"vice-city-auto"' in text
    assert "squeaking" not in text
    assert "3055550199" not in text
