"""Privacy and verification contract for repair-status lookups."""

import json
from pathlib import Path

import pytest

from test_tool_scripts import load_script

ROOT = Path(__file__).resolve().parents[1]
NEED_MSG = "I need the last name on the repair order plus the phone number on the ticket or the RO number."
FAIL_MSG = "I couldn't verify that repair order. Offer to take a message for the service writer."
NEED = {"ok": True, "found": False, "needs": ["last_name", "phone_or_ro_number"], "message": NEED_MSG}
FAIL = {"ok": True, "found": False, "message": FAIL_MSG}
SUCCESS_KEYS = {
    "ok",
    "found",
    "ro_number",
    "customer_first_name",
    "vehicle",
    "status",
    "details",
    "eta",
    "estimated_balance",
}


def lookup(client, tool_headers, payload):
    response = client.post("/tools/check_repair_status", json=payload, headers=tool_headers)
    assert response.status_code == 200
    return response


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"ro_number": "1001"},
        {"ro_number": "unknown"},
        {"phone": "3055550101"},
        {"phone": "3055550199"},
        {"phone": "3055550101", "ro_number": "1001"},
        {"plate": "MIA123"},
        {"plate": "unknown"},
        {"last_name": "Rivera"},
        {"last_name": "Unknown"},
        {"last_name": "Rivera", "plate": "MIA123"},
        {"last_name": "Unknown", "plate": "unknown"},
        {"last_name": "   ", "ro_number": "1001"},
        {"last_name": "Rivera", "phone": "   ", "ro_number": ""},
    ],
    ids=[
        "empty",
        "known-ro",
        "unknown-ro",
        "known-phone",
        "unknown-phone",
        "phone-and-ro",
        "known-plate",
        "unknown-plate",
        "known-name",
        "unknown-name",
        "name-and-plate",
        "unknown-name-and-plate",
        "blank-last-name",
        "blank-second-factors",
    ],
)
def test_missing_factors_return_data_independent_needs(client, tool_headers, payload):
    assert lookup(client, tool_headers, payload).json() == NEED


FAILURE_CASES = [
    pytest.param({"last_name": "Rivera", "ro_number": "unknown"}, id="unknown-ro"),
    pytest.param({"last_name": "Rivera", "phone": "3055550199"}, id="wrong-phone"),
    pytest.param({"last_name": "Unknown", "ro_number": "1001"}, id="wrong-last-name"),
    pytest.param({"last_name": "Rivera", "ro_number": "1001", "phone": "17865550102"}, id="ro-phone-different-orders"),
    pytest.param({"last_name": "Rivera", "ro_number": "1002", "phone": "3055550101"}, id="phone-name-different-ro"),
    pytest.param({"last_name": "Rivera", "phone": "0101"}, id="short-phone"),
]


@pytest.mark.parametrize("payload", FAILURE_CASES)
def test_verification_failure_returns_generic_response(client, tool_headers, payload):
    assert lookup(client, tool_headers, payload).json() == FAIL


@pytest.fixture
def duplicate_client(make_client, tmp_path):
    # Independent seed data: ambiguity must fail even when both factors match.
    repair = {
        "ro_number": "9001",
        "customer_name": "Demo Rivera",
        "phone": "+13055550101",
        "plate": "DEMO1",
        "vehicle": "2017 Demo Sedan",
        "status": "in_progress",
        "note": "Demo repair",
        "eta": "Tomorrow",
        "balance_estimate": "$100",
    }
    client = make_client(_seed=False)
    store = client.app.state.store
    shop = client.app.state.settings.shop_id
    store.upsert_repair_order(shop, **repair)
    store.upsert_repair_order(
        shop, **{**repair, "ro_number": "9002", "customer_name": "Other Rivera", "plate": "DEMO2"}
    )
    return client


def test_duplicate_matches_fail_verification(duplicate_client, tool_headers):
    assert lookup(duplicate_client, tool_headers, {"last_name": "Rivera", "phone": "3055550101"}).json() == FAIL


def test_all_verification_failures_are_byte_identical(client, duplicate_client, tool_headers):
    payloads = [case.values[0] for case in FAILURE_CASES]
    responses = [lookup(client, tool_headers, payload) for payload in payloads]
    responses.append(lookup(duplicate_client, tool_headers, {"last_name": "Rivera", "phone": "3055550101"}))
    assert all(response.content == responses[0].content for response in responses)
    assert responses[0].json() == FAIL


@pytest.mark.parametrize(
    "payload,ro,first_name,vehicle",
    [
        ({"last_name": "Rivera", "phone": "305-555-0101"}, "1001", "Carlos", "2017 Chevy Malibu"),
        ({"last_name": "rivera", "ro_number": " 1001 "}, "1001", "Carlos", "2017 Chevy Malibu"),
        (
            {"last_name": "RIVERA", "phone": "+1 (305) 555-0101", "ro_number": "1001"},
            "1001",
            "Carlos",
            "2017 Chevy Malibu",
        ),
        ({"last_name": "Gomez", "phone": "3055550104"}, "1004", "Ana", "2019 Honda Civic"),
        ({"last_name": "jean-baptiste", "ro_number": "1002"}, "1002", "Marie", "2015 Toyota Camry"),
        ({"last_name": "Carlos Rivera", "ro_number": "1001"}, "1001", "Carlos", "2017 Chevy Malibu"),
        ({"last_name": "Rivera", "ro_number": "1001", "plate": "UNKNOWN"}, "1001", "Carlos", "2017 Chevy Malibu"),
    ],
    ids=[
        "local-phone",
        "case-and-ro-whitespace",
        "all-factors",
        "accent-insensitive",
        "hyphenated",
        "full-name",
        "ignored-extra-plate",
    ],
)
def test_verified_status_has_only_public_fields(client, tool_headers, payload, ro, first_name, vehicle):
    result = lookup(client, tool_headers, payload).json()
    assert result["ok"] is True
    assert result["found"] is True
    assert set(result) == SUCCESS_KEYS
    assert result["ro_number"] == ro
    assert result["customer_first_name"] == first_name
    assert result["vehicle"] == vehicle
    assert result["status"]
    assert result["details"]
    assert result["eta"]
    assert result["estimated_balance"]
    if ro == "1001":
        assert result["status"] == "waiting on parts"
        assert result["estimated_balance"] == "$480"
    serialized = json.dumps(result, ensure_ascii=False)
    for private_value in (
        "Carlos Rivera",
        "Ana Gómez",
        "Marie Jean-Baptiste",
        "+13055550101",
        "+13055550104",
        "+17865550102",
    ):
        assert private_value not in serialized


def test_generator_repair_status_schema_requires_last_name():
    description, properties, required = load_script("make_tools").TOOLS["check_repair_status"]
    assert set(properties) == {"last_name", "phone", "ro_number"}
    assert required == ["last_name"]
    assert "last name" in description.lower()
    assert "RO" in description


def test_committed_repair_status_properties():
    config = json.loads((ROOT / "tools/check_repair_status.json").read_text())
    assert set(config["api_schema"]["request_body_schema"]["properties"]) == {"last_name", "phone", "ro_number"}


def test_repair_status_prompt_requires_last_name_without_plate():
    prompt = (ROOT / "agent/system_prompt.md").read_text()
    flow = prompt.split("## 2. Repair status", 1)[1].split("## 3.", 1)[0].lower()
    assert "plate" not in flow
    assert "last name" in flow


@pytest.mark.parametrize(
    "payload,outcome",
    [
        ({"last_name": "Rivera", "ro_number": "1001"}, "verified"),
        ({"last_name": "Unknown", "ro_number": "1001"}, "failed"),
        ({"ro_number": "1001"}, "needs_info"),
    ],
)
def test_lookup_records_private_audit(client, tool_headers, payload, outcome):
    import os

    from bear.db import make_engine

    from test_store import audit_rows

    lookup(client, tool_headers, payload)
    engine = make_engine(os.environ["DATABASE_URL"])
    try:
        events = audit_rows(engine)
    finally:
        engine.dispose()
    assert len(events) == 1
    event = events[0]
    assert event["shop_id"] == client.app.state.settings.shop_id
    assert event["event"] == "repair_status.lookup"
    assert event["outcome"] == outcome
    assert all(value not in str(column) for column in event.values() for value in ("Rivera", "Unknown", "+13055550101"))
