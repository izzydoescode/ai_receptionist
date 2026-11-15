"""Task 4 deadlines must stop work before a timed-out write can persist."""
import json
import logging
from pathlib import Path
import time

import main
import make_tools
import pytest

from conftest import ADMIN_AUTH
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
    path = client.app.state.settings.data_file
    original = main.read_db
    # Seed before patching so file creation cannot be mistaken for a late write.
    assert client.get("/admin", auth=ADMIN_AUTH).status_code == 200
    before = json.loads(path.read_text())[collection]

    def slow_read(path):
        time.sleep(0.6)
        return original(path)

    monkeypatch.setattr(main, "read_db", slow_read)
    with caplog.at_level(logging.DEBUG):
        start = time.monotonic()
        response = client.post(f"/tools/{tool}", json=payloads[tool], headers=tool_headers)
        elapsed = time.monotonic() - start
        # Check the eventual file even if the immediate response contract is broken.
        time.sleep(0.8)
    assert json.loads(path.read_text())[collection] == before
    assert elapsed < 0.5
    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "timeout", "message": "Sorry, our system is slow right now. Let me take a message."}
    entry = request_record(caplog, response, "POST", f"/tools/{tool}", tool)
    assert entry["error"] == "timeout"


def test_health_and_admin_have_no_tool_deadline(make_client, monkeypatch):
    client = make_client(TOOL_DEADLINE_MS="100")
    original = main.read_db
    calls = []

    def slow_read(path):
        calls.append(path)
        time.sleep(0.3)
        return original(path)

    monkeypatch.setattr(main, "read_db", slow_read)
    assert client.get("/health").status_code == 200
    start = time.monotonic()
    assert client.get("/admin", auth=ADMIN_AUTH).status_code == 200
    assert time.monotonic() - start >= 0.3
    assert calls


def test_fast_tool_under_deadline(make_client, payloads, tool_headers):
    client = make_client(TOOL_DEADLINE_MS="100")
    response = client.post("/tools/take_message", json=payloads["take_message"], headers=tool_headers)
    assert response.status_code == 200 and response.json()["saved"] is True
    assert len(json.loads(client.app.state.settings.data_file.read_text())["messages"]) == 1


@pytest.mark.parametrize("path", sorted((Path(__file__).resolve().parents[1] / "tools").glob("*.json")), ids=lambda p: p.name)
def test_committed_tool_timeout(path):
    assert json.loads(path.read_text())["response_timeout_secs"] == 10


def test_generated_tool_timeouts(tmp_path):
    paths = make_tools.main([], out_dir=tmp_path)
    assert len(paths) == 4
    assert all(json.loads(path.read_text())["response_timeout_secs"] == 10 for path in paths)
