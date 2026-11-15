import logging
import runpy
from pathlib import Path

import pytest
from fastapi import FastAPI

from conftest import ADMIN_AUTH, CONFIG_KEYS, SECRET, rows

TOOLS = ["book_appointment", "check_repair_status", "request_tow", "take_message"]


def test_import_with_empty_configuration_does_not_raise(monkeypatch):
    for key in CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "backend/main.py"))
    assert namespace["FastAPI"] is FastAPI


@pytest.mark.parametrize("secret", [None, "x" * 31])
@pytest.mark.parametrize("environment", [None, "production"])
def test_non_dev_startup_rejects_missing_or_short_secret(make_client, secret, environment):
    with pytest.raises(RuntimeError):
        make_client(TOOL_SECRET=secret, BEAR_ENV=environment)


def test_dev_without_secret_warns_and_accepts_tools(make_client, payloads, caplog):
    with caplog.at_level(logging.WARNING):
        client = make_client(TOOL_SECRET=None, BEAR_ENV="dev")
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    for name, payload in payloads.items():
        assert client.post(f"/tools/{name}", json=payload).status_code == 200


@pytest.mark.parametrize("environment", [None, "dev"])
@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("header,expected", [(None, 401), ("wrong-dummy", 401), (SECRET, 200)])
def test_each_tool_enforces_configured_secret(make_client, payloads, tool, header, expected, environment):
    client = make_client(BEAR_ENV=environment)
    headers = {} if header is None else {"X-Tool-Secret": header}
    assert client.post(f"/tools/{tool}", json=payloads[tool], headers=headers).status_code == expected


@pytest.mark.parametrize("auth", [None, ("admin", "wrong"), ("wrong", ADMIN_AUTH[1])])
def test_admin_rejects_missing_or_wrong_basic_credentials(client, auth):
    response = client.get("/admin", auth=auth)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


def test_admin_accepts_default_user_and_returns_html(client):
    response = client.get("/admin", auth=ADMIN_AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_admin_reads_custom_credentials_at_app_creation(make_client):
    first = make_client(ADMIN_USER="first", ADMIN_PASSWORD="dummy-first")
    second = make_client(ADMIN_USER="second", ADMIN_PASSWORD="dummy-second")
    assert first.get("/admin", auth=("first", "dummy-first")).status_code == 200
    assert second.get("/admin", auth=("second", "dummy-second")).status_code == 200
    assert second.get("/admin", auth=("first", "dummy-first")).status_code == 401


def test_admin_is_disabled_without_password(make_client):
    assert make_client(ADMIN_PASSWORD=None).get("/admin").status_code == 503


@pytest.mark.parametrize("environment", [None, "production"])
def test_reset_is_hidden_outside_dev(make_client, environment):
    assert make_client(BEAR_ENV=environment).post("/admin/reset", auth=ADMIN_AUTH).status_code == 404


@pytest.mark.parametrize("auth", [None, ("admin", "wrong")])
def test_dev_reset_requires_basic_auth(make_client, auth):
    response = make_client(BEAR_ENV="dev").post("/admin/reset", auth=auth)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


def test_authenticated_dev_reset_restores_seed(make_client, payloads, tool_headers, tmp_path):
    client = make_client(BEAR_ENV="dev")
    booking = client.post("/tools/book_appointment", json=payloads["book_appointment"], headers=tool_headers).json()
    assert booking["booked"] is True
    assert rows(client, "appointments")
    response = client.post("/admin/reset", auth=ADMIN_AUTH)
    assert response.status_code == 200
    assert response.json() == {"reset": True}
    data = client.app.state.store.board(client.app.state.settings.shop_id, 1000)
    assert data["appointments"] == []
    assert data["messages"] == []
    assert data["tows"] == []
    assert {row["ro_number"] for row in data["repair_orders"]} == {"1001", "1002", "1003", "1004"}
    assert booking["confirmation"] not in client.get("/admin", auth=ADMIN_AUTH).text


@pytest.mark.parametrize(
    "tool,field,raw,escaped",
    [
        ("take_message", "caller_name", "<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
        ("book_appointment", "vehicle", "<img src=x onerror=alert(1)>", "&lt;img src=x onerror=alert(1)&gt;"),
        ("request_tow", "pickup_location", "<img src=x onerror=alert(1)>", "&lt;img src=x onerror=alert(1)&gt;"),
    ],
)
def test_admin_escapes_dynamic_tool_values(client, payloads, tool_headers, tool, field, raw, escaped):
    payload = {**payloads[tool], field: raw}
    assert client.post(f"/tools/{tool}", json=payload, headers=tool_headers).status_code == 200
    body = client.get("/admin", auth=ADMIN_AUTH).text
    assert escaped in body
    assert raw not in body
    assert "<script>alert(1)" not in body


def test_shop_name_is_read_at_call_time_and_escaped_in_title_and_heading(make_client):
    first = make_client(SHOP_NAME="First Demo Shop")
    second = make_client(SHOP_NAME="<b>X</b>")
    assert "First Demo Shop" in first.get("/admin", auth=ADMIN_AUTH).text
    body = second.get("/admin", auth=ADMIN_AUTH).text
    assert "<title>&lt;b&gt;X&lt;/b&gt; board</title>" in body
    assert "<h1>&lt;b&gt;X&lt;/b&gt;: AI receptionist live board</h1>" in body
    assert "<b>X</b>" not in body


def test_health_is_public_and_reveals_only_ok_and_time(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert set(response.json()) == {"ok", "time"}
    assert response.json()["ok"] is True


def test_shop_timezone_is_read_at_app_creation(make_client):
    from datetime import datetime

    utc = make_client(SHOP_TZ="UTC")
    ny = make_client(SHOP_TZ="America/New_York")
    assert datetime.fromisoformat(utc.get("/health").json()["time"]).utcoffset().total_seconds() == 0
    assert datetime.fromisoformat(ny.get("/health").json()["time"]).utcoffset().total_seconds() in {-14400, -18000}


def test_factory_returns_fastapi(make_client):
    assert isinstance(make_client().app, FastAPI)


def test_dev_with_short_configured_secret_still_enforces_it(make_client, payloads):
    client = make_client(BEAR_ENV="dev", TOOL_SECRET="dummy-short")
    for name, payload in payloads.items():
        url = f"/tools/{name}"
        assert client.post(url, json=payload).status_code == 401
        assert client.post(url, json=payload, headers={"X-Tool-Secret": "wrong"}).status_code == 401
        assert client.post(url, json=payload, headers={"X-Tool-Secret": "dummy-short"}).status_code == 200


def test_apps_keep_their_own_secret_and_database(make_client, payloads, tmp_path):
    other_secret = "y" * 40
    first = make_client()
    second = make_client(TOOL_SECRET=other_secret)
    assert (
        first.post("/tools/take_message", json=payloads["take_message"], headers={"X-Tool-Secret": SECRET}).status_code
        == 200
    )
    assert (
        second.post("/tools/take_message", json=payloads["take_message"], headers={"X-Tool-Secret": SECRET}).status_code
        == 401
    )
    assert (
        second.post(
            "/tools/check_repair_status",
            json={"last_name": "Rivera", "ro_number": "1001"},
            headers={"X-Tool-Secret": other_secret},
        ).status_code
        == 200
    )
    assert len(rows(first, "messages")) == 1
    assert rows(second, "messages") == []


def test_non_ascii_tool_secret_header_is_rejected_not_500(client, payloads):
    response = client.post(
        "/tools/take_message",
        json=payloads["take_message"],
        headers={"X-Tool-Secret": "s\u00e9cret".encode("latin-1")},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("header", ["Basic !!!not-base64!!!", "Basic " + "YWRtaW4=", "Bearer abc"])
def test_malformed_admin_authorization_is_401_not_500(client, header):
    response = client.get("/admin", headers={"Authorization": header})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
