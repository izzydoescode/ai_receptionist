"""Replay scripted calls (tests/scenarios/*.yaml) against the backend: PLAN Task 12.

Each scenario is the sequence of tool calls the agent should make for one flow and language,
with the expected responses, database rows, audit events and /admin board contents.
See tests/scenarios/README.md for the format.
"""

import json
import time
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from bear.db import audit_events
from conftest import ADMIN_AUTH, SECRET, future_day

SCENARIOS = Path(__file__).resolve().parent / "scenarios"
BUDGET_SECONDS = 1.0
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DATES = {f"{{next_{day}}}": lambda i=i: future_day(i).isoformat() for i, day in enumerate(WEEKDAYS)}


def load(path):
    scenario = yaml.safe_load(path.read_text("utf-8"))
    assert scenario["name"] == path.stem
    return scenario


def fill_dates(value):
    if isinstance(value, str) and value in DATES:
        return DATES[value]()
    if isinstance(value, dict):
        return {key: fill_dates(item) for key, item in value.items()}
    return value


def assert_subset(expected, actual, where):
    for key, value in expected.items():
        assert key in actual, f"{where}: missing {key}"
        assert actual[key] == value, f"{where}: {key} = {actual[key]!r}, expected {value!r}"


def check_response(step, response, where):
    assert_subset(step.get("expect", {}), response, where)
    for key, fragment in step.get("expect_contains", {}).items():
        assert fragment in str(response[key]), f"{where}: {key} lacks {fragment!r}"
    for key, count in step.get("expect_length", {}).items():
        assert len(response[key]) == count, f"{where}: len({key})"
    for key in step.get("expect_missing_keys", []):
        assert key not in response, f"{where}: unexpected {key}"
    text = json.dumps(response, ensure_ascii=False)
    for fragment in step.get("expect_not_in_response", []):
        assert fragment not in text, f"{where}: leaked {fragment!r}"


def replay(client, scenario):
    headers = {"X-Tool-Secret": SECRET}
    for number, step in enumerate(scenario["calls"], 1):
        for attempt in range(step.get("repeat", 1)):
            response = client.post(f"/tools/{step['tool']}", json=fill_dates(step["args"]), headers=headers)
            assert response.status_code == 200, response.text
            if attempt == step.get("repeat", 1) - 1:
                check_response(step, response.json(), f"call {number} ({step['tool']})")


def check_database(client, scenario):
    store, shop_id = client.app.state.store, client.app.state.settings.shop_id
    board = store.board(shop_id, 1000)
    for table, count in scenario.get("db_counts", {}).items():
        assert len(board[table]) == count, f"{table}: {len(board[table])} rows, expected {count}"
    for table, expected_rows in scenario.get("db", {}).items():
        for expected in expected_rows:
            assert any(all(row.get(k) == v for k, v in expected.items()) for row in board[table]), (
                f"{table}: no row matching {expected}"
            )
    with store.engine.connect() as conn:
        events = [dict(row._mapping) for row in conn.execute(select(audit_events))]
    for expected in scenario.get("audit", []):
        assert any(all(event.get(k) == v for k, v in expected.items()) for event in events), f"audit: {expected}"


def check_admin(client, scenario):
    if "admin_contains" not in scenario:
        return
    page = client.get("/admin", auth=ADMIN_AUTH)
    assert page.status_code == 200
    for fragment in scenario["admin_contains"]:
        assert fragment in page.text, f"/admin lacks {fragment!r}"


@pytest.mark.parametrize("path", sorted(SCENARIOS.glob("*.yaml")), ids=lambda path: path.stem)
def test_scenario(make_client, path):
    scenario = load(path)
    client = make_client(_shops=[scenario.get("shop", {})])
    started = time.perf_counter()
    replay(client, scenario)
    check_database(client, scenario)
    check_admin(client, scenario)
    elapsed = time.perf_counter() - started
    assert elapsed < BUDGET_SECONDS, f"{path.stem} took {elapsed:.2f}s"


WRITE_TOOLS = {"book_appointment", "request_tow", "take_message"}


@pytest.mark.parametrize("path", sorted(SCENARIOS.glob("es_*.yaml")), ids=lambda path: path.stem)
def test_spanish_scenarios_persist_language_es(path):
    scenario = load(path)
    assert scenario["language"] == "es"
    writes = [step for step in scenario["calls"] if step["tool"] in WRITE_TOOLS]
    for step in writes:
        assert "es" in (step["args"].get("language"), step["args"].get("callback_language")), step["tool"]
    if writes:
        saved = [row for rows in scenario.get("db", {}).values() for row in rows]
        assert saved and all("es" in (row.get("language"), row.get("callback_language")) for row in saved)


PLAN_SCENARIOS = {
    "en_status",
    "es_status",
    "en_book",
    "es_book",
    "en_book_full_slot",
    "es_after_hours",
    "en_tow_unsafe",
    "es_tow_safe",
    "en_message",
    "es_message",
    "en_transfer_request",
    "en_unverified_status",
}


def test_every_plan_scenario_exists():
    assert PLAN_SCENARIOS <= {path.stem for path in SCENARIOS.glob("*.yaml")}
