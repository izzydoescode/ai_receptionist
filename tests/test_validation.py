"""Task 3 public validation contract (red phase; dummy data only)."""

import json
from datetime import datetime, timedelta

import pytest

WRITE_TOOLS = ["book_appointment", "request_tow", "take_message"]
COLLECTION = dict(zip(WRITE_TOOLS, ["appointments", "tows", "messages"], strict=True))
TEXT_LIMITS = [
    ("book_appointment", "customer_name", 80),
    ("book_appointment", "vehicle", 80),
    ("book_appointment", "service", 500),
    ("book_appointment", "notes", 500),
    ("request_tow", "customer_name", 80),
    ("request_tow", "vehicle", 80),
    ("request_tow", "pickup_location", 500),
    ("request_tow", "issue", 500),
    ("request_tow", "destination", 500),
    ("take_message", "caller_name", 80),
    ("take_message", "reason", 500),
    ("take_message", "vehicle", 80),
]
REQUIRED_TEXT = [
    (t, f) for t, f, _ in TEXT_LIMITS if f not in {"notes", "destination"} and (t, f) != ("take_message", "vehicle")
]
LANGUAGES = [("book_appointment", "language"), ("request_tow", "language"), ("take_message", "callback_language")]


def snapshot(client):
    return {
        key: client.app.state.store.board(client.app.state.settings.shop_id, 1000)[key] for key in COLLECTION.values()
    }


def assert_invalid(response, field, hint=None):
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"ok", "error", "field", "message"}
    assert result["ok"] is False
    assert result["error"] == "invalid_input"
    assert result["field"] == field
    message = result["message"]
    assert isinstance(message, str) and message.strip()
    for jargon in ["input should", "validation error", "pydantic", "type=", "field required"]:
        assert jargon not in message.lower()
    if hint:
        assert hint in message.lower()


def reject(client, tool_headers, payloads, tool, field, value, hint=None):
    before = snapshot(client)
    response = client.post(f"/tools/{tool}", headers=tool_headers, json={**payloads[tool], field: value})
    assert snapshot(client) == before, "Invalid input must not write any records"
    assert_invalid(response, field, hint)


def accept(client, tool_headers, payloads, tool, **changes):
    response = client.post(f"/tools/{tool}", headers=tool_headers, json={**payloads[tool], **changes})
    assert response.status_code == 200
    result = response.json()
    assert result.get({"book_appointment": "booked", "request_tow": "logged", "take_message": "saved"}[tool]) is True
    record = snapshot(client)[COLLECTION[tool]][0]
    assert result.get("ok") is True
    if tool == "take_message" and "vehicle" in changes:
        # Vehicle remains accepted/validated input, but is not a messages column.
        from main import TakeMessage

        return {**record, "vehicle": TakeMessage.model_validate({**payloads[tool], **changes}).vehicle}
    return record


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize("phone", ["555-0101", "123456789012", "21234567890", "", "abc", " " * 23 + "3055550199"])
def test_invalid_phone(client, tool_headers, payloads, tool, phone):
    reject(client, tool_headers, payloads, tool, "phone", phone, "10-digit" if len(phone) <= 32 else None)


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize("phone", ["(305) 555-0199", "305.555.0199", "+1 305 555 0199", "13055550199"])
def test_phone_normalized(client, tool_headers, payloads, tool, phone):
    assert accept(client, tool_headers, payloads, tool, phone=phone)["phone"] == "+13055550199"


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize(
    "phone",
    ["000-000-0000", "0005550199", "111-555-1234", "305-055-1234", "305-155-1234", "+1 000 555 0100"],
)
def test_invalid_nanp_numbers_are_rejected(client, tool_headers, payloads, tool, phone):
    reject(client, tool_headers, payloads, tool, "phone", phone, "10-digit")


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize("phone,stored", [("+509 3412 3456", "+50934123456"), ("+44 20 7946 0958", "+442079460958")])
def test_international_e164_numbers_are_accepted(client, tool_headers, payloads, tool, phone, stored):
    assert accept(client, tool_headers, payloads, tool, phone=phone)["phone"] == stored


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize("phone", ["+1234567", "+12345678", "+1234567890123456", "50934123456"])
def test_international_numbers_need_plus_and_valid_length(client, tool_headers, payloads, tool, phone):
    reject(client, tool_headers, payloads, tool, "phone", phone)


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize("phone", ["+0123456789", "+0 123 456 789", "+00000000"])
def test_e164_country_code_cannot_start_with_zero(client, tool_headers, payloads, tool, phone):
    reject(client, tool_headers, payloads, tool, "phone", phone)


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize(
    "phone",
    [
        "+509-3412-3456 ext 4",
        "+509-3412-3456 ext. 4",
        "+509-3412-3456 x4",
        "+509-3412-3456#4",
        "+509-3412-3456 extension 4",
        "305-555-0199 EXT 4",
        "(305) 555-0199 Ext. 12",
        "305-555-0199 x 12",
        "3055550199#4",
        "13055550199 Extension 4",
    ],
)
def test_phone_extensions_are_rejected(client, tool_headers, payloads, tool, phone):
    reject(client, tool_headers, payloads, tool, "phone", phone, "without the extension")


@pytest.mark.parametrize("field,limit", [("phone", 32), ("ro_number", 20), ("last_name", 80)])
def test_repair_factor_max_length(client, tool_headers, payloads, field, limit):
    reject(client, tool_headers, payloads, "check_repair_status", field, "x" * (limit + 1))


@pytest.mark.parametrize("field,limit", [("phone", 32), ("ro_number", 20), ("last_name", 80)])
def test_repair_factor_at_limit_is_business_response(client, tool_headers, field, limit):
    response = client.post(
        "/tools/check_repair_status",
        headers=tool_headers,
        json={"last_name": "Demo", "ro_number": "dummy", field: "x" * limit},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["found"] is False
    assert "error" not in response.json()


@pytest.mark.parametrize("value", ["not-a-date", "2026-13-01", "11/20/2026", "2026-02-30", "2026-1-02"])
def test_invalid_date(client, tool_headers, payloads, value):
    reject(client, tool_headers, payloads, "book_appointment", "preferred_date", value)


@pytest.mark.parametrize("value", ["9:30", "25:00", "10:60", "10am", ""])
def test_invalid_time(client, tool_headers, payloads, value):
    reject(client, tool_headers, payloads, "book_appointment", "preferred_time", value)


@pytest.fixture
def frozen_today(monkeypatch):
    import main

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # 60 days after this shop-local Monday is a Friday.
            return cls(2026, 11, 16, 12, tzinfo=tz)

    monkeypatch.setattr(main, "datetime", FrozenDateTime)
    return datetime(2026, 11, 16).date()


def test_date_beyond_60_days(client, tool_headers, payloads, frozen_today):
    reject(
        client,
        tool_headers,
        payloads,
        "book_appointment",
        "preferred_date",
        (frozen_today + timedelta(days=61)).isoformat(),
        "60 days",
    )


@pytest.mark.parametrize("time", ["10:00", "14:00"])
def test_exactly_60_days_accepted(client, tool_headers, payloads, frozen_today, time):
    accept(
        client,
        tool_headers,
        payloads,
        "book_appointment",
        preferred_date=(frozen_today + timedelta(days=60)).isoformat(),
        preferred_time=time,
    )


def test_past_date_is_business_outcome(client, tool_headers, payloads, frozen_today):
    response = client.post(
        "/tools/book_appointment",
        headers=tool_headers,
        json={**payloads["book_appointment"], "preferred_date": "2026-11-13"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["booked"] is False
    assert result["message"] == "That time already passed."
    assert isinstance(result["alternatives"], list)
    assert result["ok"] is True
    assert snapshot(client)["appointments"] == []


@pytest.mark.parametrize("tool,field", LANGUAGES)
@pytest.mark.parametrize("value", ["spanish", "pt", ""])
def test_invalid_language(client, tool_headers, payloads, tool, field, value):
    reject(client, tool_headers, payloads, tool, field, value)


@pytest.mark.parametrize("tool,field", LANGUAGES)
@pytest.mark.parametrize("value", ["en", "ES", "FR", "HT"])
def test_language_normalized(client, tool_headers, payloads, tool, field, value):
    assert accept(client, tool_headers, payloads, tool, **{field: value})[field] == value.lower()


def test_invalid_urgency(client, tool_headers, payloads):
    reject(client, tool_headers, payloads, "take_message", "urgency", "urgent")


@pytest.mark.parametrize("value", ["LOW", "normal", "HIGH"])
def test_urgency_normalized(client, tool_headers, payloads, value):
    assert accept(client, tool_headers, payloads, "take_message", urgency=value)["urgency"] == value.lower()


@pytest.mark.parametrize("tool,field", LANGUAGES)
def test_enum_defaults(client, tool_headers, payloads, tool, field):
    record = accept(client, tool_headers, payloads, tool)
    assert record[field] == "en"
    if tool == "take_message":
        assert record["urgency"] == "normal"


@pytest.mark.parametrize("tool,field,limit", TEXT_LIMITS)
def test_text_over_limit(client, tool_headers, payloads, tool, field, limit):
    reject(client, tool_headers, payloads, tool, field, "x" * (limit + 1))


@pytest.mark.parametrize("tool,field,limit", TEXT_LIMITS)
def test_text_limit_after_strip(client, tool_headers, payloads, tool, field, limit):
    assert accept(client, tool_headers, payloads, tool, **{field: "  " + "x" * limit + "  "})[field] == "x" * limit


@pytest.mark.parametrize("tool,field,limit", TEXT_LIMITS)
@pytest.mark.parametrize("control", ["\x00", "\x1b", "\n", "\t", "\x7f", "\x85"])
def test_controls_cleaned(client, tool_headers, payloads, tool, field, limit, control):
    value = f"  Ana{control} \t[31mGomez\x00 "
    assert accept(client, tool_headers, payloads, tool, **{field: value})[field] == "Ana [31mGomez"


FORMAT_CHARS = [
    "\u200b",  # ZERO WIDTH SPACE
    "\u200e",  # LEFT-TO-RIGHT MARK
    "\u200f",  # RIGHT-TO-LEFT MARK
    "\u202a",  # LEFT-TO-RIGHT EMBEDDING
    "\u202e",  # RIGHT-TO-LEFT OVERRIDE
    "\u2066",  # LEFT-TO-RIGHT ISOLATE
    "\u2069",  # POP DIRECTIONAL ISOLATE
    "\ufeff",  # BOM
]
JOINER_CHARS = [
    "\u200c",  # ZERO WIDTH NON-JOINER (Persian and other scripts)
    "\u200d",  # ZERO WIDTH JOINER (emoji sequences)
]


@pytest.mark.parametrize("tool,field,limit", TEXT_LIMITS)
@pytest.mark.parametrize("mark", FORMAT_CHARS)
def test_format_characters_are_stripped(client, tool_headers, payloads, tool, field, limit, mark):
    value = f"  Ana{mark}Gomez  "
    assert accept(client, tool_headers, payloads, tool, **{field: value})[field] == "AnaGomez"


@pytest.mark.parametrize("tool,field,limit", TEXT_LIMITS)
@pytest.mark.parametrize("mark", JOINER_CHARS)
def test_zero_width_joiners_are_kept(client, tool_headers, payloads, tool, field, limit, mark):
    value = f"  Ana{mark}Gomez  "
    assert accept(client, tool_headers, payloads, tool, **{field: value})[field] == f"Ana{mark}Gomez"


@pytest.mark.parametrize(
    "tool,field",
    [("book_appointment", "customer_name"), ("request_tow", "customer_name"), ("take_message", "caller_name")],
)
def test_persian_name_keeps_zwnj(client, tool_headers, payloads, tool, field):
    name = "علی\u200cرضا"
    assert accept(client, tool_headers, payloads, tool, **{field: f"  {name}  "})[field] == name


@pytest.mark.parametrize("tool,field", REQUIRED_TEXT)
@pytest.mark.parametrize("value", ["", " \t\n\x00 "])
def test_required_text_empty_after_cleaning(client, tool_headers, payloads, tool, field, value):
    reject(client, tool_headers, payloads, tool, field, value)


def test_non_boolean_is_safe(client, tool_headers, payloads):
    reject(client, tool_headers, payloads, "request_tow", "is_safe", "maybe")


@pytest.mark.parametrize(
    "tool,changes,first",
    [
        ("book_appointment", {"customer_name": "", "phone": "abc"}, "customer_name"),
        ("request_tow", {"phone": "abc", "issue": ""}, "phone"),
        ("take_message", {"reason": "", "urgency": "urgent"}, "reason"),
        ("check_repair_status", {"last_name": "x" * 81, "phone": "x" * 33}, "phone"),
    ],
)
def test_first_invalid_field_in_model_order(client, tool_headers, payloads, tool, changes, first):
    before = snapshot(client)
    response = client.post(f"/tools/{tool}", headers=tool_headers, json={**payloads[tool], **changes})
    assert snapshot(client) == before
    assert_invalid(response, first)


@pytest.mark.parametrize("tool", [*WRITE_TOOLS, "check_repair_status"])
@pytest.mark.parametrize(
    "kind,status", [("oversized", 413), ("plain", 415), ("missing-type", 415), ("malformed", 400), ("list", 400)]
)
def test_transport_errors(client, tool_headers, payloads, tool, kind, status):
    headers = {**tool_headers, "Content-Type": "application/json"}
    body = json.dumps(payloads[tool]).encode()
    if kind == "oversized":
        # No Content-Length: the limit must inspect actual received bytes.
        body = json.dumps({**payloads[tool], "extra": "x" * 20000}).encode()
    elif kind == "plain":
        headers["Content-Type"] = "text/plain"
    elif kind == "missing-type":
        del headers["Content-Type"]
    elif kind == "malformed":
        body = b'{"broken":'
    elif kind == "list":
        body = b"[]"
    before = snapshot(client)
    response = client.post(f"/tools/{tool}", headers=headers, content=iter([body]) if kind == "oversized" else body)
    assert snapshot(client) == before
    assert response.status_code == status
    if status == 400:
        assert response.json() == {"detail": "invalid JSON body"}


@pytest.mark.parametrize("tool", [*WRITE_TOOLS, "check_repair_status"])
def test_body_at_16384_bytes_with_charset_accepted(client, tool_headers, payloads, tool):
    # JSON whitespace pads a valid request without violating field limits.
    body = json.dumps(payloads[tool]).encode()
    body += b" " * (16384 - len(body))
    assert len(body) == 16384
    response = client.post(
        f"/tools/{tool}", content=body, headers={**tool_headers, "Content-Type": "application/json; charset=utf-8"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
