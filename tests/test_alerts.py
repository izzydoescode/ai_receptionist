"""Log-only staff alerts for bookings, messages and tows."""

import logging

import pytest

from bear.alerts import PROD_LOG_WARNING

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


def alert_text(caplog):
    return "\n".join(record.getMessage() for record in caplog.records if record.name == "bear.alerts")


def post_event(client, tool_headers, tool, payload):
    response = client.post(f"/tools/{tool}", json=payload, headers=tool_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("ok") is True
    return body


@pytest.mark.parametrize("env,simulated", [("dev", True), ("staging", True), ("prod", False)])
@pytest.mark.parametrize(
    "tool,payload,phrase",
    [
        ("book_appointment", BOOKING, "booked"),
        ("take_message", MESSAGE, "left a message"),
        ("request_tow", TOW, "requested a tow"),
    ],
)
def test_staff_alert_block_follows_the_environment(
    make_client, tool_headers, caplog, payloads, env, simulated, tool, payload, phrase
):
    client = make_client(BEAR_ENV=env)
    body = dict(payload)
    if tool == "book_appointment":
        body["preferred_date"] = payloads["book_appointment"]["preferred_date"]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        post_event(client, tool_headers, tool, body)
    text = alert_text(caplog)
    assert "Daniel Brooks" in text
    assert "**-***-0142" in text
    assert phrase in text
    assert "Dear owner" in text
    assert "Your virtual receptionist, Bear." in text
    assert "3055550142" not in text
    assert "5550142" not in text
    assert "transcript" not in text.lower()
    if simulated:
        assert "EMAIL SENT! (simulated; no real email went out)" in text
        assert "TEXT SENT! (simulated; no real text went out)" in text
    else:
        assert "EMAIL SENT!" in text
        assert "TEXT SENT!" in text
        assert "simulated" not in text
        assert "no real email went out" not in text
        assert "no real text went out" not in text


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_prod_log_mode_warns_once_at_startup(make_client, caplog, env):
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bear"):
        make_client(BEAR_ENV=env)
    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    if env == "prod":
        assert PROD_LOG_WARNING in warnings
        assert warnings.count(PROD_LOG_WARNING) == 1
    else:
        assert PROD_LOG_WARNING not in warnings


def test_alerts_mode_defaults_to_log(make_client, tool_headers, caplog, payloads):
    client = make_client()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bear.alerts"):
        post_event(
            client,
            tool_headers,
            "take_message",
            {**MESSAGE, "reason": payloads["take_message"]["reason"]},
        )
    assert "EMAIL SENT!" in alert_text(caplog)


def test_alerts_mode_off_does_not_log_or_warn(make_client, tool_headers, caplog):
    caplog.clear()
    with caplog.at_level(logging.INFO):
        client = make_client(BEAR_ENV="prod", ALERTS_MODE="off")
        post_event(client, tool_headers, "take_message", MESSAGE)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "EMAIL SENT!" not in text
    assert "TEXT SENT!" not in text
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
