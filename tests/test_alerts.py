"""Staff alerts for bookings, messages and tows."""

import logging

import pytest

from bear import alerts
from bear.alerts import EMAIL_HEADER, LOG_ONLY_HEADER, PROD_LOG_WARNING

BOOKING = {
    "shop_id": "vice-city-auto",
    "customer_name": "Daniel Brooks",
    "phone": "3055550142",
    "vehicle": "Demo sedan",
    "service": "oil change",
    "preferred_time": "10:00",
}
MESSAGE = {
    "shop_id": "vice-city-auto",
    "caller_name": "Daniel Brooks",
    "phone": "3055550142",
    "reason": "Please call back about the Demo sedan",
}
TOW = {
    "shop_id": "vice-city-auto",
    "customer_name": "Daniel Brooks",
    "phone": "3055550142",
    "vehicle": "Demo sedan",
    "pickup_location": "Demo intersection",
    "issue": "flat tire",
    "is_safe": True,
}
EVENTS = [
    ("book_appointment", BOOKING, "booking", "confirmation"),
    ("take_message", MESSAGE, "message", "ticket"),
    ("request_tow", TOW, "tow", "ticket"),
]


def alert_text(caplog):
    return "\n".join(record.getMessage() for record in caplog.records if record.name == "bear.alerts")


def event_body(tool, payload, payloads):
    body = dict(payload)
    if tool == "book_appointment":
        body["preferred_date"] = payloads["book_appointment"]["preferred_date"]
    return body


def post_event(client, tool_headers, tool, payload):
    response = client.post(f"/tools/{tool}", json=payload, headers=tool_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("ok") is True, body
    return body


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
@pytest.mark.parametrize("tool,payload,kind,id_field", EVENTS)
def test_log_alert_names_kind_shop_and_ticket_only(
    make_client, tool_headers, caplog, payloads, env, tool, payload, kind, id_field
):
    client = make_client(BEAR_ENV=env)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        body = post_event(client, tool_headers, tool, event_body(tool, payload, payloads))
    text = alert_text(caplog)
    assert text == f"{LOG_ONLY_HEADER}: new {kind} for shop vice-city-auto, ticket {body[id_field]}"
    for private in ("Daniel", "Brooks", "0142", "Demo sedan", "Demo intersection", "flat tire", "Please call"):
        assert private not in text


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_log_mode_never_claims_delivery(make_client, tool_headers, caplog, env):
    client = make_client(BEAR_ENV=env)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        post_event(client, tool_headers, "take_message", MESSAGE)
    text = alert_text(caplog)
    assert "not delivered" in text
    assert "SENT" not in text.upper()


@pytest.mark.parametrize("tool,payload,kind,id_field", EVENTS)
def test_failing_alert_does_not_change_the_tool_result(
    make_client, tool_headers, caplog, payloads, monkeypatch, tool, payload, kind, id_field
):
    def broken_line(*args, **kwargs):
        raise RuntimeError("alert backend is down")

    monkeypatch.setattr(alerts, "log_only_line", broken_line)
    client = make_client()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        body = post_event(client, tool_headers, tool, event_body(tool, payload, payloads))
    assert body[id_field]
    assert body.get("booked", True) is True
    failures = [record for record in caplog.records if record.name == "bear.alerts" and record.levelno == logging.ERROR]
    assert len(failures) == 1
    assert f"staff alert failed for {kind}" in failures[0].getMessage()


def test_unknown_alert_kind_is_reported_not_raised(caplog):
    alerter = alerts.StaffAlerter("log")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        alerter.send("fax", alerts.AlertTarget("vice-city-auto", "Vice City Auto", []), "X1", {})
    assert "staff alert failed for fax X1" in alert_text(caplog)


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_prod_log_mode_warns_once_at_startup(make_client, caplog, env):
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bear"):
        make_client(BEAR_ENV=env)
    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    if env == "prod":
        assert warnings.count(PROD_LOG_WARNING) == 1
    else:
        assert PROD_LOG_WARNING not in warnings


def test_alerts_mode_defaults_to_log(make_client, tool_headers, caplog):
    client = make_client()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        post_event(client, tool_headers, "take_message", MESSAGE)
    assert LOG_ONLY_HEADER in alert_text(caplog)


def test_alerts_mode_off_does_not_log_or_warn(make_client, tool_headers, caplog):
    caplog.clear()
    with caplog.at_level(logging.INFO):
        client = make_client(BEAR_ENV="prod", ALERTS_MODE="off")
        post_event(client, tool_headers, "take_message", MESSAGE)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert LOG_ONLY_HEADER not in text
    assert PROD_LOG_WARNING not in text


def test_failed_booking_does_not_alert(make_client, tool_headers, caplog, payloads):
    client = make_client()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        response = client.post(
            "/tools/book_appointment",
            json={
                **BOOKING,
                "preferred_date": payloads["book_appointment"]["preferred_date"],
                "preferred_time": "10:30",
            },
            headers=tool_headers,
        )
    assert response.status_code == 200
    assert response.json()["ok"] is False or response.json().get("booked") is False
    assert alert_text(caplog) == ""


STAFF = ["owner@shop.example", "desk@shop.example"]
EMAIL_ENV = {
    "ALERTS_MODE": "email",
    "ALERTS_EMAIL_API_KEY": "re_dummy_key",
    "ALERTS_EMAIL_FROM": "Bear <alerts@shop.example>",
    "PUBLIC_BASE_URL": "https://bear.example.test",
}


@pytest.fixture
def email_client(make_client):
    """An email-mode app whose sender records emails instead of calling Resend."""
    sent = []

    def make(sender=None, shops=({"alerts": {"email": STAFF}},), **env):
        client = make_client(_shops=shops, **{**EMAIL_ENV, **env})
        client.app.state.alerter.email_sender = sender or (lambda to, subject, text: sent.append((to, subject, text)))
        return client

    make.sent = sent
    return make


def audit_outcomes(client, public_id):
    from sqlalchemy import select

    from bear.db import audit_events

    with client.app.state.store.engine.connect() as conn:
        rows = conn.execute(
            select(audit_events.c.event, audit_events.c.outcome).where(audit_events.c.entity_public_id == public_id)
        ).all()
    return [tuple(row) for row in rows if row[0] == "alert.email"]


@pytest.mark.parametrize("key", ["ALERTS_EMAIL_API_KEY", "ALERTS_EMAIL_FROM"])
def test_email_mode_needs_a_key_and_a_sender_address(make_client, key):
    with pytest.raises(RuntimeError, match="ALERTS_EMAIL_API_KEY and ALERTS_EMAIL_FROM"):
        make_client(**{**EMAIL_ENV, key: None})


def test_unknown_alerts_mode_stops_startup(make_client):
    with pytest.raises(RuntimeError, match="off, log or email"):
        make_client(ALERTS_MODE="sms")


def test_message_email_reaches_staff_with_callback_details(email_client, tool_headers, caplog):
    client = email_client()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        body = post_event(
            client,
            tool_headers,
            "take_message",
            {**MESSAGE, "urgency": "high", "callback_language": "es", "vehicle": "Demo sedan"},
        )
        client.app.state.alerter.wait_idle()
    [(to, subject, text)] = email_client.sent
    assert to == STAFF
    assert subject == "[Vice City Auto] New message (HIGH, call back in Spanish): Daniel Brooks"
    for expected in (
        f"ticket {body['ticket']}",
        "Phone: +13055550142",
        "Reason: Please call back about the Demo sedan",
        "Urgency: high",
        "Call back in: Spanish",
        "Live board: https://bear.example.test/admin?shop=vice-city-auto",
    ):
        assert expected in text
    logged = alert_text(caplog)
    assert f"{EMAIL_HEADER}: new message for shop vice-city-auto, ticket {body['ticket']}" in logged
    assert "STAFF ALERT emailed" in logged
    for private in ("Daniel", "0142", "Please call", "owner@shop.example"):
        assert private not in logged
    assert audit_outcomes(client, body["ticket"]) == [("alert.email", "sent")]


def test_unsafe_tow_is_flagged_in_the_subject(email_client, tool_headers):
    client = email_client()
    post_event(client, tool_headers, "request_tow", {**TOW, "is_safe": False})
    client.app.state.alerter.wait_idle()
    [(_, subject, text)] = email_client.sent
    assert "Tow request (CALLER MAY BE UNSAFE)" in subject
    assert "Caller safe: NO, they may be in danger" in text
    assert "Pickup location: Demo intersection" in text


def test_booking_email_says_when(email_client, tool_headers, payloads):
    client = email_client()
    body = post_event(client, tool_headers, "book_appointment", event_body("book_appointment", BOOKING, payloads))
    client.app.state.alerter.wait_idle()
    [(_, subject, text)] = email_client.sent
    assert subject.startswith("[Vice City Auto] New booking: Daniel Brooks")
    assert f"When: {body['when']}" in text
    assert "Service: oil change" in text


def test_failed_email_never_changes_the_tool_result(email_client, tool_headers, caplog):
    def broken(to, subject, text):
        raise RuntimeError("email API returned HTTP 500")

    client = email_client(sender=broken)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        body = post_event(client, tool_headers, "take_message", MESSAGE)
        client.app.state.alerter.wait_idle()
    assert body["saved"] is True
    assert "STAFF ALERT email failed for message" in alert_text(caplog)
    assert "HTTP 500" in alert_text(caplog)
    assert audit_outcomes(client, body["ticket"]) == [("alert.email", "failed")]


def test_slow_email_does_not_hold_up_the_caller(email_client, tool_headers):
    import threading
    import time

    release = threading.Event()

    def slow(to, subject, text):
        release.wait(5)

    client = email_client(sender=slow, TOOL_DEADLINE_MS="1000")
    started = time.monotonic()
    body = post_event(client, tool_headers, "take_message", MESSAGE)
    assert time.monotonic() - started < 1
    assert body["saved"] is True
    release.set()
    client.app.state.alerter.wait_idle()


def test_shop_without_recipients_warns_and_sends_nothing(email_client, tool_headers, caplog):
    caplog.clear()
    with caplog.at_level(logging.INFO):
        client = email_client(shops=({},))
        post_event(client, tool_headers, "take_message", MESSAGE)
    assert email_client.sent == []
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "lists no alerts.email recipients" in text
    assert "STAFF ALERT not delivered: shop vice-city-auto has no alerts.email recipients" in text


def test_unknown_number_messages_go_to_the_default_shop_staff(email_client, tool_headers):
    client = email_client()
    post_event(client, tool_headers, "take_message", {**MESSAGE, "shop_id": "unknown"})
    client.app.state.alerter.wait_idle()
    [(to, subject, text)] = email_client.sent
    assert to == STAFF
    assert subject.startswith("[Unrecognized number] New message")
    assert "admin?shop=unknown" in text


def test_resend_sender_posts_json_with_a_bearer_key(monkeypatch):
    import json

    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id": "email_1"}'

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, headers=dict(request.headers), body=json.loads(request.data), timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    alerts.resend_sender("re_dummy_key", "Bear <alerts@shop.example>")(STAFF, "Subject", "Body")
    assert seen["url"] == "https://api.resend.com/emails"
    assert seen["headers"]["Authorization"] == "Bearer re_dummy_key"
    assert seen["body"] == {"from": "Bear <alerts@shop.example>", "to": STAFF, "subject": "Subject", "text": "Body"}
    assert seen["timeout"] == alerts.EMAIL_TIMEOUT_SECONDS


def test_resend_errors_report_only_the_status(monkeypatch):
    import io
    import urllib.error

    def failing(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 422, "bad", {}, io.BytesIO(b'{"to": "owner@shop.example"}'))

    monkeypatch.setattr(alerts.urllib.request, "urlopen", failing)
    with pytest.raises(RuntimeError) as error:
        alerts.resend_sender("re_dummy_key", "alerts@shop.example")(STAFF, "S", "B")
    assert str(error.value) == "email API returned HTTP 422"


@pytest.mark.parametrize("address", ["not-an-email", "a@b", "Owner <owner@shop.example>", "a@b.c,d@e.f"])
def test_shop_file_rejects_bad_alert_addresses(make_client, address):
    with pytest.raises(RuntimeError):
        make_client(_shops=({"alerts": {"email": [address]}},))
