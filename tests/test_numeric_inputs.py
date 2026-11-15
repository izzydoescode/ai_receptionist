"""LLMs sometimes send phone and RO numbers as JSON numbers; accept them."""

import json


def test_numeric_phone_is_normalized(client, tool_headers, payloads):
    response = client.post(
        "/tools/take_message", headers=tool_headers, json={**payloads["take_message"], "phone": 3055550199}
    )
    assert response.json()["ok"] is True
    data = json.loads(client.app.state.settings.data_file.read_text())
    assert data["messages"][-1]["phone"] == "+13055550199"


def test_numeric_repair_factors_verify(client, tool_headers):
    response = client.post(
        "/tools/check_repair_status",
        headers=tool_headers,
        json={"last_name": "Rivera", "ro_number": 1001, "phone": 3055550101},
    )
    assert response.json()["found"] is True
    assert response.json()["ro_number"] == "1001"


def test_boolean_phone_is_still_invalid(client, tool_headers, payloads):
    response = client.post(
        "/tools/take_message", headers=tool_headers, json={**payloads["take_message"], "phone": True}
    )
    assert response.json()["ok"] is False
    assert response.json()["field"] == "phone"
