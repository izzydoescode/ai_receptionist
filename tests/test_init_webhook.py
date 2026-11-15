import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bear.initiation import InitResponse
from conftest import INIT_SECRET, SECRET

ROOT = Path(__file__).resolve().parents[1]
URL = "/elevenlabs/init"
HEADERS = {"X-Init-Webhook-Secret": INIT_SECRET}
WEST = {
    "shop_id": "west-shop",
    "name": "West Demo Shop",
    "city": "Doral",
    "languages": ["es", "en", "fr"],
    "persona": {"name": "Gaby"},
}


def assert_recent_local(text, zone):
    started = datetime.strptime(text, "%A, %B %d, %Y, %I:%M %p").replace(tzinfo=ZoneInfo(zone))
    assert abs((datetime.now(ZoneInfo(zone)) - started).total_seconds()) < 120


def body(called_number="+13055550110", **extra):
    return {
        "caller_id": "+13055550199",
        "called_number": called_number,
        "agent_id": "agent_demo",
        "call_sid": "CA" + "0" * 32,
        "conversation_id": "conv_demo",
        **extra,
    }


@pytest.fixture
def init_client(make_client):
    return make_client(_seed=False, _shops=[{}, WEST])


def init(client, payload, headers=HEADERS):
    response = client.post(URL, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    InitResponse.model_validate(data)
    return data


def test_known_number_returns_that_shops_variables_and_overrides(init_client):
    shop = init_client.app.state.shops["vice-city-auto"]
    data = init(init_client, body("+13055550110"))
    assert data["type"] == "conversation_initiation_client_data"
    today = datetime.now(ZoneInfo("America/New_York")).date()
    assert data["dynamic_variables"] == {
        "shop_id": "vice-city-auto",
        "shop_name": "Vice City Auto",
        "shop_city": "Miami",
        "shop_address": shop.address,
        "hours_text": shop.hours_text(today),
        "services_text": shop.services_text(),
        "persona_name": "Bear",
        "features_text": shop.features_text(),
        "shop_timezone": "America/New_York",
        "languages_text": "English, Spanish and French",
        "call_start_local": data["dynamic_variables"]["call_start_local"],
        "callback_text": shop.callback_text(),
    }
    assert data["dynamic_variables"]["callback_text"].startswith("Staff can call back in English and Spanish.")
    assert_recent_local(data["dynamic_variables"]["call_start_local"], "America/New_York")
    assert data["conversation_config_override"] == {
        "agent": {"first_message": shop.first_messages["en"], "language": "en"},
        "tts": {"voice_id": "PLACEHOLDER_VOICE_EN"},
    }


def test_second_shop_is_found_by_its_own_number_and_primary_language(init_client):
    data = init(init_client, body("+13055550111"))
    assert data["dynamic_variables"]["shop_id"] == "west-shop"
    assert data["dynamic_variables"]["shop_city"] == "Doral"
    assert data["dynamic_variables"]["persona_name"] == "Gaby"
    assert data["dynamic_variables"]["languages_text"] == "Spanish, English and French"
    override = data["conversation_config_override"]
    assert override["agent"]["language"] == "es"
    assert "puede ser grabada" in override["agent"]["first_message"]
    assert override["tts"] == {"voice_id": "PLACEHOLDER_VOICE_ES"}


@pytest.mark.parametrize("number", ["(305) 555-0110", "13055550110", "3055550110"])
def test_called_number_formats_are_normalized(init_client, number):
    assert init(init_client, body(number))["dynamic_variables"]["shop_id"] == "vice-city-auto"


@pytest.mark.parametrize("number", ["+13055550999", "", "not-a-number", "+442071234567"])
def test_unknown_number_gets_a_safe_message_only_greeting(init_client, number):
    data = init(init_client, body(number))
    variables = data["dynamic_variables"]
    assert variables["shop_id"] == "unknown"
    assert variables["shop_timezone"] == "America/New_York"  # the default shop's zone
    assert variables["languages_text"] == "English"
    # Nobody is known to staff this line, so promise nothing beyond an English callback.
    assert variables["callback_text"].startswith("Staff can call back in English.")
    assert "Kreyòl ap rele" not in variables["callback_text"]
    assert_recent_local(variables["call_start_local"], "America/New_York")
    assert "Vice City" not in str(data)
    assert "take a message" in variables["features_text"]
    agent = data["conversation_config_override"]["agent"]
    assert agent["language"] == "en"
    assert "may be recorded" in agent["first_message"]
    assert "virtual assistant" not in agent["first_message"].lower()
    assert variables["persona_name"] == "the receptionist"
    assert "tts" not in data["conversation_config_override"]


def test_missing_called_number_is_treated_as_unknown(init_client):
    payload = body()
    del payload["called_number"]
    assert init(init_client, payload)["dynamic_variables"]["shop_id"] == "unknown"


def test_extra_fields_such_as_sip_headers_are_ignored(init_client):
    data = init(init_client, body(call_id="sip-1", sip_headers={"X-Demo": "1"}))
    assert data["dynamic_variables"]["shop_id"] == "vice-city-auto"


@pytest.mark.parametrize("headers", [{}, {"X-Init-Webhook-Secret": "wrong"}, {"X-Tool-Secret": SECRET}])
def test_missing_or_wrong_secret_is_401(init_client, headers):
    response = init_client.post(URL, json=body(), headers=headers)
    assert response.status_code == 401
    assert "Vice City" not in response.text


def test_tool_secret_is_not_accepted_as_init_secret(init_client):
    response = init_client.post(URL, json=body(), headers={"X-Init-Webhook-Secret": SECRET})
    assert response.status_code == 401


def test_dev_without_init_secret_is_open(make_client):
    client = make_client(_seed=False, BEAR_ENV="dev", INIT_WEBHOOK_SECRET=None)
    assert init(client, body(), headers={})["dynamic_variables"]["shop_id"] == "vice-city-auto"


@pytest.mark.parametrize(
    "content,status",
    [
        (b"not json", 400),
        (b"[1, 2]", 400),
        (b'{"called_number": 5}', 400),
        (b'{"called_number": "' + b"1" * 20000 + b'"}', 413),
    ],
)
def test_malformed_bodies_are_rejected_after_auth(init_client, content, status):
    assert init_client.post(URL, content=content).status_code == 401
    response = init_client.post(URL, content=content, headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == status


def test_every_prompt_placeholder_is_provided(init_client):
    prompt = (ROOT / "agent/system_prompt.md").read_text("utf-8")
    names = set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", prompt))
    custom = {name for name in names if not name.startswith("system__")}
    assert custom, "expected shop placeholders in the prompt"
    for number in ("+13055550110", "+13055550999"):
        assert custom <= set(init(init_client, body(number))["dynamic_variables"])


def test_init_responds_well_under_the_ring_budget(init_client):
    init(init_client, body())  # warm up
    timings = []
    for number in ("+13055550110", "+13055550111", "+13055550999") * 10:
        start = time.perf_counter()
        init(init_client, body(number))
        timings.append(time.perf_counter() - start)
    # ElevenLabs fetches during the ring; the target is 300 ms. In-process calls are far below it.
    assert max(timings) < 0.3
