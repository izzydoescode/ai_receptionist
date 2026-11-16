"""Staff alerts for bookings, messages and tows."""

import logging

import pytest

from bear import alerts
from bear.alerts import LOG_ONLY_HEADER, PROD_LOG_WARNING

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
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        alerts.send_staff_alert("log", "fax", shop_id="vice-city-auto", public_id="X1")
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
