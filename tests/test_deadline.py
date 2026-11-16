"""Task 4 deadlines must stop work before a timed-out write can persist."""

import json
import logging
import time
from pathlib import Path

import pytest

import main
import make_tools
from conftest import ADMIN_AUTH, rows
from test_logging import request_record


@pytest.mark.parametrize("value", ["abc", "1.5", "", "0", "-1"])
def test_invalid_deadline_rejected(make_client, value):
    with pytest.raises(RuntimeError):
        make_client(TOOL_DEADLINE_MS=value)


def test_default_deadline_allows_request(make_client, monkeypatch, payloads, tool_headers):
    monkeypatch.delenv("TOOL_DEADLINE_MS", raising=False)
    client = make_client()
    response = client.post("/tools/take_message", json=payloads["take_message"], headers=tool_headers)
    assert response.status_code == 200 and response.json()["saved"] is True


@pytest.mark.parametrize("tool,collection", [("take_message", "messages"), ("book_appointment", "appointments")])
def test_deadline_does_not_persist_late(make_client, monkeypatch, payloads, tool_headers, caplog, tool, collection):
    client = make_client(TOOL_DEADLINE_MS="100")
    store = client.app.state.store
    method = "create_message" if tool == "take_message" else "book_appointment"
    original = getattr(store, method)
    before = rows(client, collection)

    def slow_write(*args, **kwargs):
        time.sleep(0.6)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, method, slow_write)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        start = time.monotonic()
        response = client.post(f"/tools/{tool}", json=payloads[tool], headers=tool_headers)
        elapsed = time.monotonic() - start
        # Check eventual storage even if the immediate response contract is broken.
        time.sleep(0.8)
    assert rows(client, collection) == before
    assert elapsed < 0.5
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error": "timeout",
        "message": "Sorry about the wait. Our system's running a little slow right now. Let me take a message so someone can call you back.",
    }
    entry = request_record(caplog, response, "POST", f"/tools/{tool}", tool)
    assert entry["error"] == "timeout"


def test_health_and_admin_have_no_tool_deadline(make_client, monkeypatch):
    client = make_client(TOOL_DEADLINE_MS="100")
    original = client.app.state.store.board
    calls = []

    def slow_read(*args, **kwargs):
        calls.append(args)
        time.sleep(0.3)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.store, "board", slow_read)
    assert client.get("/health").status_code == 200
    start = time.monotonic()
    assert client.get("/admin", auth=ADMIN_AUTH).status_code == 200
    assert time.monotonic() - start >= 0.3
    assert calls


def test_fast_tool_under_deadline(make_client, payloads, tool_headers):
    client = make_client(TOOL_DEADLINE_MS="100")
    response = client.post("/tools/take_message", json=payloads["take_message"], headers=tool_headers)
    assert response.status_code == 200 and response.json()["saved"] is True
    assert len(rows(client, "messages")) == 1


@pytest.mark.parametrize(
    "path", sorted((Path(__file__).resolve().parents[1] / "tools").glob("*.json")), ids=lambda p: p.name
)
def test_committed_tool_timeout(path):
    assert json.loads(path.read_text())["response_timeout_secs"] == 10


def test_generated_tool_timeouts(tmp_path):
    paths = make_tools.main(out_dir=tmp_path, environ={"PUBLIC_BASE_URL": "https://example.test"})
    assert len(paths) == 4
    assert all(json.loads(path.read_text())["response_timeout_secs"] == 10 for path in paths)


def test_write_is_open_respects_abandoned_flag_before_deadline():
    # A timed-out worker can reach save() while the wall clock still looks
    # slightly early; the abandoned flag must close the write anyway.
    token = main._REQUEST_BOUND.set({"deadline": time.monotonic() + 30, "abandoned": False})
    try:
        assert main.write_is_open() is True
        main._REQUEST_BOUND.get()["abandoned"] = True
        assert main.write_is_open() is False
    finally:
        main._REQUEST_BOUND.reset(token)


def test_abandoned_worker_does_not_clobber_newer_write(tmp_path):
    # A timed-out worker may still hold a stale snapshot after a newer request
    # has already rewritten the file. persist_json must refuse that write.
    path = tmp_path / "data.json"
    path.write_text(json.dumps({"messages": [{"reason": "newer message"}]}, indent=2), encoding="utf-8")
    token = main._REQUEST_BOUND.set({"deadline": time.monotonic() + 30, "abandoned": True})
    try:
        with pytest.raises(main._DeadlineExceeded):
            main.persist_json(path, {"messages": [{"reason": "stale message"}]})
    finally:
        main._REQUEST_BOUND.reset(token)
    assert json.loads(path.read_text())["messages"] == [{"reason": "newer message"}]


@pytest.mark.parametrize(
    "tool,flag", [("take_message", "saved"), ("request_tow", "logged"), ("book_appointment", "booked")]
)
def test_write_cycle_bails_when_the_request_is_no_longer_open(
    make_client, monkeypatch, payloads, tool_headers, tool, flag
):
    client = make_client()
    collections = ("messages", "tows", "appointments")
    before = {name: rows(client, name) for name in collections}
    monkeypatch.setattr(main, "write_is_open", lambda: False)
    response = client.post(f"/tools/{tool}", json=payloads[tool], headers=tool_headers)
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error": "internal_error",
        "message": "Sorry about that. Something went wrong on our end. Let me take a message so someone can call you back.",
    }
    assert flag not in response.json()
    assert {name: rows(client, name) for name in collections} == before
