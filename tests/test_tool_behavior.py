from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import future_day, rows


def post(client, tool_headers, tool, payload):
    response = client.post(f"/tools/{tool}", json=payload, headers=tool_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    return response.json()


def test_booking_next_weekday_persists_confirmation(make_client, tmp_path, payloads, tool_headers):
    client = make_client()
    result = post(client, tool_headers, "book_appointment", payloads["book_appointment"])
    assert result["booked"] is True
    assert result["confirmation"].startswith("A")
    (appointment,) = rows(client, "appointments")
    assert appointment["public_id"] == result["confirmation"]
    for key, value in payloads["book_appointment"].items():
        if key in {"preferred_date", "preferred_time"}:
            local = appointment["slot_start"].astimezone(ZoneInfo("America/New_York"))
            assert (local.date().isoformat() if key == "preferred_date" else local.strftime("%H:%M")) == value
            continue
        assert appointment[key] == ("+13055550199" if key == "phone" else value)


@pytest.mark.parametrize("case", ["sunday", "early_weekday", "late_saturday", "past"])
def test_unavailable_booking_returns_business_failure(client, payloads, tool_headers, case):
    payload = payloads["book_appointment"].copy()
    if case == "sunday":
        payload["preferred_date"] = future_day(6).isoformat()
    elif case == "early_weekday":
        payload["preferred_time"] = "07:00"
    elif case == "late_saturday":
        payload.update(preferred_date=future_day(5).isoformat(), preferred_time="15:00")
    else:
        past = datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=7)
        while past.weekday() >= 5:
            past -= timedelta(days=1)
        payload["preferred_date"] = past.isoformat()
    result = post(client, tool_headers, "book_appointment", payload)
    assert result["booked"] is False
    assert isinstance(result["alternatives"], list)
    if case == "sunday":
        assert "closed sundays" in result["message"].lower()


def test_third_booking_in_same_hour_is_full_with_three_alternatives(client, payloads, tool_headers):
    payload = payloads["book_appointment"]
    assert post(client, tool_headers, "book_appointment", payload)["booked"] is True
    # Capacity is per hour, including bookings at a different minute.
    assert post(client, tool_headers, "book_appointment", {**payload, "preferred_time": "10:30"})["booked"] is True
    result = post(client, tool_headers, "book_appointment", payload)
    assert result["booked"] is False
    assert result["message"] == "That slot is full."
    assert len(result["alternatives"]) == 3


@pytest.mark.parametrize("shop_name", [None, "Another Demo Shop"])
def test_safe_tow_is_logged_with_shop_as_default_destination(make_client, tmp_path, payloads, tool_headers, shop_name):
    client = make_client(SHOP_NAME=shop_name)
    result = post(client, tool_headers, "request_tow", payloads["request_tow"])
    assert result["logged"] is True
    assert result["ticket"].startswith("T")
    (tow,) = rows(client, "tows")
    assert tow["public_id"] == result["ticket"]
    assert tow["destination"] == (shop_name or "Vice City Auto")


def test_unsafe_tow_starts_with_911_warning(client, payloads, tool_headers):
    result = post(client, tool_headers, "request_tow", {**payloads["request_tow"], "is_safe": False})
    assert result["logged"] is True
    assert result["message"].startswith("Caller may be unsafe: tell them to call 911 now")


@pytest.mark.parametrize(
    "language,sentence",
    [
        ("es", "Alguien que habla español le llamará."),
        ("ht", "A Creole-speaking team member will call back."),
    ],
)
def test_message_ticket_and_requested_callback_language(client, payloads, tool_headers, language, sentence):
    result = post(client, tool_headers, "take_message", {**payloads["take_message"], "callback_language": language})
    assert result["saved"] is True
    assert result["ticket"].startswith("M")
    assert sentence in result["message"]


@pytest.mark.parametrize(
    "tool,field",
    [
        ("book_appointment", field)
        for field in ["customer_name", "phone", "vehicle", "service", "preferred_date", "preferred_time"]
    ]
    + [("request_tow", field) for field in ["customer_name", "phone", "vehicle", "pickup_location", "issue", "is_safe"]]
    + [("take_message", field) for field in ["caller_name", "phone", "reason"]],
)
def test_missing_required_tool_field_returns_validation_envelope(client, payloads, tool_headers, tool, field):
    payload = payloads[tool].copy()
    del payload[field]
    from test_validation import assert_invalid, snapshot

    before = snapshot(client)
    response = client.post(f"/tools/{tool}", json=payload, headers=tool_headers)
    assert snapshot(client) == before
    assert_invalid(response, field)
