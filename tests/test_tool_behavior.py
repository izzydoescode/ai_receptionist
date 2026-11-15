import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import future_day


def post(client, tool_headers, tool, payload):
    response = client.post(f"/tools/{tool}", json=payload, headers=tool_headers)
    assert response.status_code == 200
    return response.json()


def test_booking_next_weekday_persists_confirmation(make_client, tmp_path, payloads, tool_headers):
    path = tmp_path / "appointments.json"
    client = make_client(DATA_FILE=path)
    result = post(client, tool_headers, "book_appointment", payloads["book_appointment"])
    assert result["booked"] is True
    assert result["confirmation"].startswith("A")
    appointment, = json.loads(path.read_text())["appointments"]
    assert appointment["id"] == result["confirmation"]
    for key, value in payloads["book_appointment"].items():
        assert appointment[key] == value


@pytest.mark.parametrize("case", ["bad_date", "sunday", "early_weekday", "late_saturday", "past"])
def test_unavailable_booking_returns_business_failure(client, payloads, tool_headers, case):
    payload = payloads["book_appointment"].copy()
    if case == "bad_date":
        payload["preferred_date"] = "not-a-date"
    elif case == "sunday":
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


@pytest.mark.parametrize("identifier", [
    {"ro_number": "1001"}, {"phone": "305-555-0101"},
    {"plate": "mia 123"}, {"last_name": "Rivera"},
])
def test_repair_status_matches_existing_identifiers_without_full_name_or_phone(client, tool_headers, identifier):
    result = post(client, tool_headers, "check_repair_status", identifier)
    assert result["found"] is True
    assert result["ro_number"] == "1001"
    assert result["customer_first_name"] == "Carlos"
    assert result["vehicle"] == "2017 Chevy Malibu"
    assert result["status"] == "waiting on parts"
    assert result["details"]
    assert result["eta"]
    assert result["estimated_balance"] == "$480"
    assert "phone" not in result
    assert "customer" not in result
    serialized = json.dumps(result)
    assert "Carlos Rivera" not in serialized
    assert "3055550101" not in serialized
    assert "305-555-0101" not in serialized


def test_unknown_repair_is_not_found(client, tool_headers):
    assert post(client, tool_headers, "check_repair_status", {"ro_number": "missing-demo"})["found"] is False


@pytest.mark.parametrize("shop_name", [None, "Another Demo Shop"])
def test_safe_tow_is_logged_with_shop_as_default_destination(make_client, tmp_path, payloads, tool_headers, shop_name):
    path = tmp_path / "tows.json"
    client = make_client(DATA_FILE=path, SHOP_NAME=shop_name)
    result = post(client, tool_headers, "request_tow", payloads["request_tow"])
    assert result["logged"] is True
    assert result["ticket"].startswith("T")
    tow, = json.loads(path.read_text())["tows"]
    assert tow["id"] == result["ticket"]
    assert tow["destination"] == (shop_name or "Vice City Auto")


def test_unsafe_tow_starts_with_911_warning(client, payloads, tool_headers):
    result = post(client, tool_headers, "request_tow", {**payloads["request_tow"], "is_safe": False})
    assert result["logged"] is True
    assert result["message"].startswith("Caller may be unsafe: tell them to call 911 now")


@pytest.mark.parametrize("language,sentence", [
    ("es", "Alguien que habla español le llamará."),
    ("ht", "A Creole-speaking team member will call back."),
])
def test_message_ticket_and_requested_callback_language(client, payloads, tool_headers, language, sentence):
    result = post(client, tool_headers, "take_message", {**payloads["take_message"], "callback_language": language})
    assert result["saved"] is True
    assert result["ticket"].startswith("M")
    assert sentence in result["message"]


@pytest.mark.parametrize("tool,field", [
    ("book_appointment", field) for field in
    ["customer_name", "phone", "vehicle", "service", "preferred_date", "preferred_time"]
] + [
    ("request_tow", field) for field in
    ["customer_name", "phone", "vehicle", "pickup_location", "issue", "is_safe"]
] + [("take_message", field) for field in ["caller_name", "phone", "reason"]])
def test_missing_required_tool_field_returns_422(client, payloads, tool_headers, tool, field):
    payload = payloads[tool].copy()
    del payload[field]
    assert client.post(f"/tools/{tool}", json=payload, headers=tool_headers).status_code == 422
