import pytest

from conftest import future_day, rows
from test_validation import snapshot

TOOLS = ["book_appointment", "check_repair_status", "request_tow", "take_message"]
WEST = {
    "shop_id": "west-shop",
    "name": "West Demo Shop",
    "hours": {
        "monday": [],
        "tuesday": [{"open": "08:00", "close": "18:00"}],
        "wednesday": [{"open": "08:00", "close": "18:00"}],
        "thursday": [{"open": "08:00", "close": "18:00"}],
        "friday": [{"open": "08:00", "close": "18:00"}],
        "saturday": [],
        "sunday": [],
    },
}


@pytest.fixture
def two_shops(make_client):
    client = make_client(_shops=[{}, WEST])
    client.app.state.store.upsert_repair_order(
        "west-shop",
        ro_number="7001",
        customer_name="Demo Westley",
        phone="+13055550177",
        plate="DEMO77",
        vehicle="2015 Demo Coupe",
        status="in_progress",
        note="demo note",
        eta="Friday",
        balance_estimate="$100",
    )
    return client


def call(client, tool_headers, tool, payload):
    response = client.post(f"/tools/{tool}", json=payload, headers=tool_headers)
    assert response.status_code == 200
    return response.json()


def board(client, shop):
    return client.app.state.store.board(shop, 1000)


def test_tools_write_to_the_requested_shop(two_shops, payloads, tool_headers):
    tuesday_booking = {
        **payloads["book_appointment"],
        "shop_id": "west-shop",
        "preferred_date": future_day(1).isoformat(),
    }
    assert call(two_shops, tool_headers, "book_appointment", tuesday_booking)["booked"] is True
    tow = call(two_shops, tool_headers, "request_tow", {**payloads["request_tow"], "shop_id": "west-shop"})
    assert tow["logged"] is True
    assert call(two_shops, tool_headers, "take_message", {**payloads["take_message"], "shop_id": "west-shop"})["saved"]
    west = board(two_shops, "west-shop")
    assert [len(west[t]) for t in ("appointments", "tows", "messages")] == [1, 1, 1]
    assert west["tows"][0]["destination"] == "West Demo Shop"
    assert rows(two_shops, "appointments") == rows(two_shops, "tows") == rows(two_shops, "messages") == []


def test_repair_status_only_searches_the_requested_shop(two_shops, tool_headers):
    lookup = {"last_name": "Westley", "ro_number": "7001"}
    assert call(two_shops, tool_headers, "check_repair_status", {**lookup, "shop_id": "west-shop"})["found"] is True
    assert (
        call(two_shops, tool_headers, "check_repair_status", {**lookup, "shop_id": "vice-city-auto"})["found"] is False
    )
    rivera = {"last_name": "Rivera", "ro_number": "1001"}
    assert (
        call(two_shops, tool_headers, "check_repair_status", {**rivera, "shop_id": "vice-city-auto"})["found"] is True
    )
    assert call(two_shops, tool_headers, "check_repair_status", {**rivera, "shop_id": "west-shop"})["found"] is False


def test_booking_uses_the_requested_shops_hours(two_shops, payloads, tool_headers):
    monday = payloads["book_appointment"]
    closed = call(two_shops, tool_headers, "book_appointment", {**monday, "shop_id": "west-shop"})
    assert closed["message"] == "We're closed Mondays."
    assert closed["alternatives"][0].startswith("Tuesday")
    assert call(two_shops, tool_headers, "book_appointment", {**monday, "shop_id": "vice-city-auto"})["booked"] is True


@pytest.mark.parametrize("tool", TOOLS)
def test_missing_shop_id_is_rejected_when_more_than_one_shop_is_loaded(two_shops, payloads, tool_headers, tool):
    before = snapshot(two_shops)
    result = call(two_shops, tool_headers, tool, payloads[tool])
    assert result == {
        "ok": False,
        "error": "invalid_input",
        "field": "shop_id",
        "message": result["message"],
    }
    assert "shop id is missing" in result["message"].lower()
    assert snapshot(two_shops) == before
    assert rows(two_shops, "appointments") == rows(two_shops, "tows") == rows(two_shops, "messages") == []


def test_missing_shop_id_is_rejected_outside_dev(make_client, payloads, tool_headers):
    client = make_client(_shops=[{}], BEAR_ENV="prod")
    before = snapshot(client)
    result = call(client, tool_headers, "take_message", payloads["take_message"])
    assert result["ok"] is False
    assert result["error"] == "invalid_input"
    assert result["field"] == "shop_id"
    assert snapshot(client) == before


def test_missing_shop_id_defaults_only_for_single_shop_dev(make_client, payloads, tool_headers):
    client = make_client(_shops=[{}], BEAR_ENV="dev")
    result = call(client, tool_headers, "take_message", payloads["take_message"])
    assert result["ok"] is True and result["saved"] is True
    assert rows(client, "messages")[0]["shop_id"] == "vice-city-auto"


@pytest.mark.parametrize("tool", TOOLS)
def test_unknown_shop_returns_business_error_and_writes_nothing(two_shops, payloads, tool_headers, tool):
    before = snapshot(two_shops)
    audit_before = len(audit(two_shops))
    result = call(two_shops, tool_headers, tool, {**payloads[tool], "shop_id": "no-such-shop"})
    assert result["ok"] is False
    assert result["error"] == "unknown_shop"
    assert result["message"]
    assert snapshot(two_shops) == before
    assert not any(board(two_shops, "no-such-shop").values())
    assert len(audit(two_shops)) == audit_before


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("shop_id", ["Bad Shop", "../etc", "", "a\n", 7])
def test_malformed_shop_id_is_invalid_input(two_shops, payloads, tool_headers, tool, shop_id):
    result = call(two_shops, tool_headers, tool, {**payloads[tool], "shop_id": shop_id})
    assert result["ok"] is False
    assert result["error"] == "invalid_input"
    assert result["field"] == "shop_id"


def audit(client):
    from sqlalchemy import select

    from bear.db import audit_events

    with client.app.state.store.engine.connect() as conn:
        return conn.execute(select(audit_events)).all()


def test_unknown_number_calls_can_leave_a_message(two_shops, payloads, tool_headers):
    result = call(
        two_shops,
        tool_headers,
        "take_message",
        {**payloads["take_message"], "shop_id": "unknown", "conversation_id": "conv_demo-1"},
    )
    assert result["ok"] is True and result["saved"] is True
    (message,) = board(two_shops, "unknown")["messages"]
    assert message["public_id"] == result["ticket"]
    assert message["conversation_id"] == "conv_demo-1"
    assert rows(two_shops, "messages") == []


def test_take_message_stores_conversation_id_and_shows_it_on_the_board(client, payloads, tool_headers):
    from conftest import ADMIN_AUTH

    result = call(
        client,
        tool_headers,
        "take_message",
        {**payloads["take_message"], "conversation_id": "conv_demo-2"},
    )
    assert result["saved"] is True
    (message,) = rows(client, "messages")
    assert message["conversation_id"] == "conv_demo-2"
    board_page = client.get("/admin", auth=ADMIN_AUTH)
    assert board_page.status_code == 200
    assert "conv_demo-2" in board_page.text


@pytest.mark.parametrize("tool", ["book_appointment", "check_repair_status", "request_tow"])
def test_unknown_number_calls_cannot_use_other_tools(two_shops, payloads, tool_headers, tool):
    audit_before = len(audit(two_shops))
    result = call(two_shops, tool_headers, tool, {**payloads[tool], "shop_id": "unknown"})
    assert result["ok"] is False
    assert result["error"] == "unknown_shop"
    assert "take a message" in result["message"]
    assert not any(board(two_shops, "unknown").values())
    assert len(audit(two_shops)) == audit_before
