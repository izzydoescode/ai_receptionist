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
    assert post(client, tool_headers, "book_appointment", payload)["booked"] is True
    result = post(client, tool_headers, "book_appointment", payload)
    assert result["booked"] is False
    assert result["message"] == "That slot is full."
    assert len(result["alternatives"]) == 3


def test_off_hour_booking_time_is_rejected(client, payloads, tool_headers):
    response = client.post(
        "/tools/book_appointment",
        json={**payloads["book_appointment"], "preferred_time": "10:30"},
        headers=tool_headers,
    )
    assert response.status_code == 200
    result = response.json()
    assert result == {
        "ok": False,
        "error": "invalid_input",
        "field": "preferred_time",
        "message": result["message"],
    }
    assert "starts a slot" in result["message"].lower()
    assert rows(client, "appointments") == []


@pytest.mark.parametrize("shop_name", [None, "Another Demo Shop"])
def test_safe_tow_is_logged_with_shop_as_default_destination(make_client, tmp_path, payloads, tool_headers, shop_name):
    client = make_client(_shops=[{"name": shop_name}] if shop_name else ())
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


TOW_ON = {"sms_confirmations": False, "tow_dispatch": True, "photo_texting": False}
BANNED_WHEN_OFF = ["15 minutes", "dispatcher", "text photos", "24/7"]


def test_tow_without_dispatch_promises_only_a_callback(client, payloads, tool_headers):
    message = post(client, tool_headers, "request_tow", payloads["request_tow"])["message"]
    assert message == (
        "Tow request logged. Tell the caller: \"I've logged it and the shop will call you back as soon as possible. "
        "If you're unsafe, call 911.\""
    )
    for phrase in BANNED_WHEN_OFF:
        assert phrase not in message


def test_tow_with_dispatch_promises_the_dispatcher(make_client, payloads, tool_headers):
    client = make_client(_shops=[{"features": TOW_ON}])
    message = post(client, tool_headers, "request_tow", payloads["request_tow"])["message"]
    assert message == "Tow request logged; dispatcher will call back within 15 minutes with an ETA."


def test_unsafe_tow_without_dispatch_keeps_the_911_warning_first(client, payloads, tool_headers):
    message = post(client, tool_headers, "request_tow", {**payloads["request_tow"], "is_safe": False})["message"]
    assert message.startswith("Caller may be unsafe: tell them to call 911 now")
    assert "15 minutes" not in message


@pytest.mark.parametrize("shop_id", ["vice-city-auto", "unknown"])
def test_message_reply_promises_only_a_callback(client, payloads, tool_headers, shop_id):
    message = post(client, tool_headers, "take_message", {**payloads["take_message"], "shop_id": shop_id})["message"]
    assert message.startswith("Message saved for the service writer. The shop will call back as soon as possible.")
    for phrase in BANNED_WHEN_OFF:
        assert phrase not in message


STAFFED_SENTENCES = {
    "es": "Alguien que habla español le llamará.",
    "fr": "A French-speaking team member will call back.",
    "ht": "A Creole-speaking team member will call back.",
}
LANGUAGE_NAMES = {"es": "Spanish", "fr": "French", "ht": "Haitian Creole"}


def leave_message(client, tool_headers, payloads, language, shop_id="vice-city-auto"):
    payload = {**payloads["take_message"], "callback_language": language, "shop_id": shop_id}
    result = post(client, tool_headers, "take_message", payload)
    assert result["saved"] is True
    assert result["ticket"].startswith("M")
    return result["message"]


@pytest.mark.parametrize("language", sorted(STAFFED_SENTENCES))
def test_message_promises_a_callback_in_a_staffed_language(make_client, payloads, tool_headers, language):
    client = make_client(_shops=[{"callback_languages": ["en", "es", "fr", "ht"]}])
    assert STAFFED_SENTENCES[language] in leave_message(client, tool_headers, payloads, language)


@pytest.mark.parametrize("language", sorted(STAFFED_SENTENCES))
def test_message_never_promises_an_unstaffed_language(make_client, payloads, tool_headers, language):
    client = make_client(_shops=[{"callback_languages": ["en"]}])
    message = leave_message(client, tool_headers, payloads, language)
    assert STAFFED_SENTENCES[language] not in message
    assert f"Don't promise a {LANGUAGE_NAMES[language]}-speaking callback" in message
    assert "The shop will call back as soon as possible." in message


def test_demo_shop_promises_spanish_but_not_creole(client, payloads, tool_headers):
    assert STAFFED_SENTENCES["es"] in leave_message(client, tool_headers, payloads, "es")
    assert STAFFED_SENTENCES["ht"] not in leave_message(client, tool_headers, payloads, "ht")


@pytest.mark.parametrize("language", sorted(STAFFED_SENTENCES))
def test_unknown_line_promises_no_language_callback(client, payloads, tool_headers, language):
    message = leave_message(client, tool_headers, payloads, language, shop_id="unknown")
    assert STAFFED_SENTENCES[language] not in message


def test_english_callback_adds_nothing(client, payloads, tool_headers):
    message = leave_message(client, tool_headers, payloads, "en")
    assert message == "Message saved for the service writer. The shop will call back as soon as possible."


def test_take_message_persists_vehicle_and_shows_it_on_the_board(client, payloads, tool_headers):
    from conftest import ADMIN_AUTH

    vehicle = "2012 Ford F-150"
    result = post(client, tool_headers, "take_message", {**payloads["take_message"], "vehicle": vehicle})
    assert result["saved"] is True
    (message,) = rows(client, "messages")
    assert message["vehicle"] == vehicle
    board = client.get("/admin", auth=ADMIN_AUTH)
    assert board.status_code == 200
    assert vehicle in board.text


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


CLOSED_WEEK = {day: [] for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}


def test_booking_follows_configured_hours_slot_size_and_bays(make_client, payloads, tool_headers):
    hours = {**CLOSED_WEEK, "monday": [{"open": "10:00", "close": "12:00"}]}
    client = make_client(_shops=[{"hours": hours, "slot_minutes": 30, "bays_per_slot": 1}])
    payload = {**payloads["book_appointment"], "preferred_time": "10:15"}
    off_grid = client.post("/tools/book_appointment", json=payload, headers=tool_headers).json()
    assert off_grid["ok"] is False
    assert off_grid["error"] == "invalid_input"
    assert off_grid["field"] == "preferred_time"
    assert rows(client, "appointments") == []
    payload["preferred_time"] = "10:00"
    assert post(client, tool_headers, "book_appointment", payload)["booked"] is True
    (appointment,) = rows(client, "appointments")
    assert appointment["slot_start"].astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") == "10:00"
    result = post(client, tool_headers, "book_appointment", payload)
    assert result["message"] == "That slot is full."
    assert [a.split(" at ")[1] for a in result["alternatives"]] == ["10:30 AM", "11:00 AM", "11:30 AM"]
    closed = post(client, tool_headers, "book_appointment", {**payload, "preferred_time": "12:00"})
    assert closed["message"] == "That day we take drop-offs from 10:00 AM to 12:00 PM."


def test_booking_on_a_holiday_is_refused_and_skipped(make_client, payloads, tool_headers):
    monday = future_day(0)
    client = make_client(_shops=[{"holidays": [monday.isoformat()]}])
    result = post(client, tool_headers, "book_appointment", payloads["book_appointment"])
    assert result["booked"] is False
    assert result["message"] == "We're closed that day for a holiday."
    assert result["alternatives"][0].startswith("Tuesday")
    assert rows(client, "appointments") == []
