"""Task 4 request logging and privacy contract (intentionally red)."""

import base64
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import ADMIN_AUTH, SECRET

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "Zelda Quixote",
    "Quixote",
    "1999 Secret Wagon",
    "123 Hidden Street",
    "PRIVATE-ISSUE-TEXT",
    "PRIVATE-REASON-TEXT",
    "PRIVATE-NOTES-TEXT",
    "(305) 555-0199",
    "3055550199",
    "305-555-0199",
    "+13055550199",
    "5550199",
    SECRET,
    "WRONG-SECRET-VALUE",
    ADMIN_AUTH[1],
    "Basic WRONG-AUTH-VALUE",
    "Basic " + base64.b64encode(":".join(ADMIN_AUTH).encode()).decode(),
)


def assert_private(caplog):
    # httpx records are TestClient's own client-side request lines, not app logs.
    texts = []
    for record in caplog.records:
        if record.name.startswith("httpx"):
            continue
        texts.append(logging.Formatter("%(name)s %(message)s").format(record))
        texts.extend((record.getMessage(), repr(record.args), record.exc_text or ""))
        if record.exc_info:
            texts.append(str(record.exc_info[1]))
    for value in FORBIDDEN:
        assert all(value not in text for text in texts), f"log leaked {value!r}"


def request_record(caplog, response, method, path, tool=None):
    records = [r for r in caplog.records if r.name == "bear.request"]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.INFO
    line = record.getMessage()
    assert "\n" not in line and "\r" not in line
    entry = json.loads(line)
    assert isinstance(entry, dict)
    assert {
        "ts",
        "level",
        "event",
        "request_id",
        "method",
        "path",
        "tool",
        "status",
        "latency_ms",
        "ok",
    } <= entry.keys()
    assert datetime.fromisoformat(entry["ts"]).utcoffset() == timedelta(0)
    assert entry["level"] == "info" and entry["event"] == "request"
    assert entry["method"] == method and entry["path"] == path
    assert entry["tool"] == tool
    assert entry["status"] == response.status_code
    assert isinstance(entry["latency_ms"], (int, float)) and not isinstance(entry["latency_ms"], bool)
    assert entry["latency_ms"] >= 0
    expected_ok = response.json()["ok"] if tool and response.status_code == 200 else 200 <= response.status_code < 300
    assert entry["ok"] is expected_ok
    assert response.headers["X-Request-Id"] == entry["request_id"]
    return entry


def private_payload(payloads, tool):
    payload = dict(payloads[tool])
    replacements = dict(
        customer_name="Zelda Quixote",
        caller_name="Zelda Quixote",
        last_name="Quixote",
        phone="(305) 555-0199",
        vehicle="1999 Secret Wagon",
        service="PRIVATE-ISSUE-TEXT",
        pickup_location="123 Hidden Street",
        issue="PRIVATE-ISSUE-TEXT",
        reason="PRIVATE-REASON-TEXT",
    )
    payload.update({k: v for k, v in replacements.items() if k in payload})
    if tool == "book_appointment":
        payload["notes"] = "PRIVATE-NOTES-TEXT"
    if tool == "check_repair_status":
        payload.update(last_name="Quixote", phone="(305) 555-0199")
    return payload


@pytest.mark.parametrize(
    "case",
    [
        "book_appointment",
        "check_repair_status",
        "request_tow",
        "take_message",
        "validation",
        "auth",
        "transport",
        "admin",
        "admin_auth",
        "health",
    ],
)
def test_request_logging_and_redaction(case, client, payloads, tool_headers, caplog):
    with caplog.at_level(logging.DEBUG):
        tool = case if case in payloads else "take_message"
        payload = private_payload(payloads, tool)
        headers = dict(tool_headers, **{"X-Request-Id": "request_123", "X-Conversation-Id": "conv.123-test"})
        error = None
        if case in {"admin", "admin_auth", "health"}:
            path = "/health" if case == "health" else "/admin"
            headers["Authorization"] = FORBIDDEN[-1] if case == "admin" else "Basic WRONG-AUTH-VALUE"
            response = client.get(path + "?phone=3055550199", headers=headers)
            method, tool = "GET", None
            if case == "admin_auth":
                error = "unauthorized"
        else:
            method, path = "POST", f"/tools/{tool}"
            if case == "validation":
                payload["phone"] = "abc"
                error = "invalid_input"
            if case == "auth":
                headers["X-Tool-Secret"] = "WRONG-SECRET-VALUE"
                error = "unauthorized"
            if case == "transport":
                response = client.post(
                    path, content=json.dumps(payload), headers={**headers, "Content-Type": "text/plain"}
                )
                error = "transport"
            else:
                response = client.post(path, json=payload, headers=headers)
        assert_private(caplog)
        assert response.status_code == {"auth": 401, "admin_auth": 401, "transport": 415}.get(case, 200)
        entry = request_record(caplog, response, method, path, tool)
        assert entry["request_id"] == "request_123"
        assert entry["conversation_id"] == "conv.123-test"
        if error:
            assert entry["error"] == error
        else:
            assert "error" not in entry
        if case == "auth":
            assert "phone" not in entry
        elif case == "validation":
            assert entry["field"] == "phone"
            assert entry.get("phone") in (None, "***")
        elif case in payloads:
            assert entry["phone"] == "***0199"


@pytest.mark.parametrize(
    "request_id,conversation_id",
    [(None, None), ("", ""), ("bad id", "bad/id"), ("x" * 65, "x" * 65), ("ok.A_1-z", "ok.A_1-z")],
)
def test_correlation_header_validation(client, caplog, request_id, conversation_id):
    headers = {} if request_id is None else {"X-Request-Id": request_id, "X-Conversation-Id": conversation_id}
    with caplog.at_level(logging.DEBUG):
        response = client.get("/health", headers=headers)
    entry = request_record(caplog, response, "GET", "/health")
    if request_id == "ok.A_1-z":
        assert entry["request_id"] == request_id
        assert entry["conversation_id"] == conversation_id
    else:
        assert re.fullmatch(r"[0-9a-f]{32}", entry["request_id"])
        assert entry.get("conversation_id") is None


@pytest.mark.parametrize(
    "case,status,error",
    [
        ("unknown", 404, "not_found"),
        ("malformed", 400, "transport"),
        ("oversized", 413, "transport"),
        ("unavailable", 503, "unavailable"),
        ("reset", 404, "not_found"),
    ],
)
def test_error_response_records(make_client, tool_headers, caplog, case, status, error):
    if case == "unavailable":
        client = make_client(ADMIN_PASSWORD=None)
    elif case == "reset":
        client = make_client(BEAR_ENV="prod")
    else:
        client = make_client()
    with caplog.at_level(logging.DEBUG):
        if case in {"unavailable", "reset"}:
            method, path = ("GET", "/admin") if case == "unavailable" else ("POST", "/admin/reset")
            response = client.request(method, path)
            tool = None
        else:
            method = "POST"
            tool = None if case == "unknown" else "take_message"
            path = "/tools/" + ("unknown" if tool is None else tool)
            body = "{}" if case == "unknown" else ("{" if case == "malformed" else json.dumps({"reason": "x" * 17000}))
            response = client.post(path, content=body, headers={**tool_headers, "Content-Type": "application/json"})
    assert response.status_code == status
    entry = request_record(caplog, response, method, path, tool)
    assert entry["error"] == error
    assert re.fullmatch(r"[0-9a-f]{32}", entry["request_id"])


def test_repair_phone_mask(client, tool_headers, caplog):
    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/tools/check_repair_status", json={"last_name": "Quixote", "phone": "305-555-0101"}, headers=tool_headers
        )
    entry = request_record(caplog, response, "POST", "/tools/check_repair_status", "check_repair_status")
    assert entry["phone"] == "***0101"


def test_international_phone_is_masked(client, payloads, tool_headers, caplog):
    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/tools/take_message",
            json={**payloads["take_message"], "phone": "+509 3412 3456"},
            headers=tool_headers,
        )
    assert response.status_code == 200 and response.json()["saved"] is True
    entry = request_record(caplog, response, "POST", "/tools/take_message", "take_message")
    assert entry["phone"] == "***3456"
    texts = [r.getMessage() for r in caplog.records if r.name.startswith("bear")]
    assert all("+50934123456" not in text and "509 3412 3456" not in text for text in texts)


def test_unauthenticated_tool_requests_do_not_log_a_phone(client, payloads, caplog):
    payload = {**payloads["take_message"], "phone": "+509 3412 3456"}
    requests = [
        {},
        {"headers": {"X-Tool-Secret": "WRONG-SECRET-VALUE"}},
    ]
    texts = []
    for kwargs in requests:
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            response = client.post("/tools/take_message", json=payload, **kwargs)
        assert response.status_code == 401
        entry = request_record(caplog, response, "POST", "/tools/take_message", "take_message")
        assert entry["error"] == "unauthorized"
        assert "phone" not in entry
        texts.extend(r.getMessage() for r in caplog.records if r.name.startswith("bear"))
    assert all("+50934123456" not in text and "509 3412 3456" not in text and "34123456" not in text for text in texts)


def test_unauthenticated_bad_transport_still_returns_401(client, payloads, caplog):
    payload = json.dumps({**payloads["take_message"], "phone": "305-555-0199"})
    cases = [
        {"content": payload, "headers": {"Content-Type": "text/plain"}},
        {"content": "{", "headers": {"Content-Type": "application/json"}},
        {"content": json.dumps({"reason": "x" * 17000}), "headers": {"Content-Type": "application/json"}},
    ]
    texts = []
    for case in cases:
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            response = client.post("/tools/take_message", **case)
        assert response.status_code == 401
        entry = request_record(caplog, response, "POST", "/tools/take_message", "take_message")
        assert entry["error"] == "unauthorized"
        assert "phone" not in entry
        texts.extend(r.getMessage() for r in caplog.records if r.name.startswith("bear"))
    assert all("3055550199" not in text and "305-555-0199" not in text for text in texts)


def test_unhandled_error_is_safe(client, payloads, tool_headers, monkeypatch, caplog):
    def broken_read(*args, **kwargs):
        raise RuntimeError("Zelda Quixote 3055550199")

    monkeypatch.setattr(client.app.state.store, "create_message", broken_read)
    with caplog.at_level(logging.DEBUG):
        response = client.post("/tools/take_message", json=payloads["take_message"], headers=tool_headers)
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error": "internal_error",
        "message": "Sorry, something went wrong on our end. Let me take a message.",
    }
    assert_private(caplog)
    entry = request_record(caplog, response, "POST", "/tools/take_message", "take_message")
    assert entry["error"] == "internal_error"
    assert entry["exc_type"] == "RuntimeError"


def test_stdout_handler_is_idempotent(make_client):
    make_client()
    make_client()
    assert len([h for h in logging.getLogger("bear").handlers if h.name == "bear-json"]) == 1


def test_request_json_written_to_stdout(tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "backend"),
        "TOOL_SECRET": "x" * 40,
        "INIT_WEBHOOK_SECRET": "y" * 40,
        "DATABASE_URL": f"sqlite:///{tmp_path}/stdout.db",
    }
    env.pop("TOOL_DEADLINE_MS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; from bear.db import create_schema, make_engine; create_schema(make_engine(os.environ['DATABASE_URL'])); from main import create_app; from fastapi.testclient import TestClient; c = TestClient(create_app()); assert c.get('/health').status_code == 200; c.close()",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    entries = []
    for line in result.stdout.splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            # Non-JSON lines (uvicorn/other output) are ignored.
            continue
    assert any(isinstance(e, dict) and e.get("event") == "request" and e.get("path") == "/health" for e in entries)


@pytest.mark.parametrize(
    "body_shop,logged_shop,error",
    [
        (None, "vice-city-auto", "invalid_input"),
        ("null", "vice-city-auto", "invalid_input"),
        ("west-shop", "west-shop", None),
        ("no-such-shop", "no-such-shop", "unknown_shop"),
        ("Bad Shop", None, "invalid_input"),
        (7, None, "invalid_input"),
    ],
)
def test_tool_logs_carry_the_resolved_shop_id(
    make_client, payloads, tool_headers, caplog, body_shop, logged_shop, error
):
    client = make_client(_shops=[{}, {"shop_id": "west-shop"}])
    payload = dict(payloads["take_message"])
    if body_shop is not None:
        payload["shop_id"] = None if body_shop == "null" else body_shop
    with caplog.at_level(logging.INFO):
        response = client.post("/tools/take_message", json=payload, headers=tool_headers)
    entry = request_record(caplog, response, "POST", "/tools/take_message", "take_message")
    assert entry.get("shop_id") == logged_shop
    assert entry.get("error") == error


def test_non_tool_logs_have_no_shop_id(client, caplog):
    with caplog.at_level(logging.INFO):
        response = client.get("/health")
    assert "shop_id" not in request_record(caplog, response, "GET", "/health")


@pytest.mark.parametrize(
    "called,headers_ok,shop",
    [("+13055550110", True, "vice-city-auto"), ("+13055550999", True, "unknown"), ("+13055550110", False, None)],
)
def test_init_webhook_logs_shop_and_conversation_but_no_numbers(make_client, caplog, called, headers_ok, shop):
    from conftest import INIT_SECRET

    client = make_client(_seed=False)
    headers = {"X-Init-Webhook-Secret": INIT_SECRET if headers_ok else "wrong"}
    payload = {
        "caller_id": "+13055550199",
        "called_number": called,
        "agent_id": "agent_demo",
        "call_sid": "CA123",
        "conversation_id": "conv_demo-1",
    }
    with caplog.at_level(logging.INFO):
        response = client.post("/elevenlabs/init", json=payload, headers=headers)
    entry = request_record(caplog, response, "POST", "/elevenlabs/init")
    assert entry.get("shop_id") == shop
    assert entry.get("conversation_id") == ("conv_demo-1" if headers_ok else None)
    assert_private(caplog)
    line = next(r.getMessage() for r in caplog.records if r.name == "bear.request")
    assert "5550110" not in line and "5550999" not in line
