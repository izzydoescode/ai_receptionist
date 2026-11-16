import re
from pathlib import Path

import pytest

PROMPT = (Path(__file__).resolve().parents[1] / "agent/system_prompt.md").read_text("utf-8")
HARD_CODED_HOURS = [
    r"\b\d{1,2}(:\d{2})?\s?(AM|PM|A\.M\.|P\.M\.)\b",  # 8 AM, 6:00 PM
    r"\b\d{1,2}:\d{2}\b",  # 09:00
    r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s*(-|–|to|through)\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)",  # Mon-Fri
    r"\bclosed (on )?(Sundays?|Saturdays?|Mondays?)\b",
]


@pytest.mark.parametrize("pattern", ["vice city", "YOUR-NGROK-ID", "America/New_York", "Miami"])
def test_prompt_has_no_hard_coded_shop_facts(pattern):
    assert pattern.lower() not in PROMPT.lower()


@pytest.mark.parametrize("pattern", HARD_CODED_HOURS)
def test_prompt_has_no_hard_coded_hours(pattern):
    assert re.search(pattern, PROMPT, re.IGNORECASE) is None


@pytest.mark.parametrize(
    "variable",
    [
        "shop_name",
        "shop_city",
        "shop_address",
        "hours_text",
        "services_text",
        "persona_name",
        "features_text",
        "shop_timezone",
        "languages_text",
        "call_start_local",
    ],
)
def test_prompt_uses_shop_dynamic_variables(variable):
    assert "{{" + variable + "}}" in PROMPT


def test_prompt_avoids_per_turn_system_time():
    # {{system__time}} follows the agent's single time zone and changes every turn.
    assert "{{system__time}}" not in PROMPT


def test_persona_name_is_not_hard_coded():
    assert '"Bear,"' not in PROMPT
    assert "this is Bear" not in PROMPT


ALL_OFF = {"sms_confirmations": False, "tow_dispatch": False, "photo_texting": False}
ALL_ON = {"sms_confirmations": True, "tow_dispatch": True, "photo_texting": True}
# Promises that are only true when a feature is on (or never true, like "24/7").
PHOTO_PROMISES = ["text photos", "text them photos", "text us photos"]
TOW_PROMISES = ["15 minutes", "dispatcher will call", "dispatch calls back"]
NEVER = ["24/7", "calendar works"]


def rendered_prompt(features):
    from datetime import UTC, datetime

    from bear.initiation import shop_initiation
    from bear.shops import ShopConfig
    from test_shop_config import config

    shop = ShopConfig.model_validate(config(features=features))
    variables = shop_initiation(shop, datetime(2026, 12, 1, 15, tzinfo=UTC)).dynamic_variables.model_dump()
    text = PROMPT
    for name, value in variables.items():
        text = text.replace("{{" + name + "}}", str(value))
    assert "{{" not in text.replace("{{system__", "")
    return text.lower()


@pytest.mark.parametrize("phrase", PHOTO_PROMISES + TOW_PROMISES + NEVER)
def test_no_false_promises_when_features_are_off(phrase):
    assert phrase.lower() not in rendered_prompt(ALL_OFF)


@pytest.mark.parametrize("phrase", NEVER)
def test_never_promises_a_24_7_calendar(phrase):
    assert phrase.lower() not in rendered_prompt(ALL_ON)


def test_features_off_prompt_gives_the_fallbacks():
    text = rendered_prompt(ALL_OFF)
    assert "the shop will call you back as soon as possible" in text
    assert "call 911" in text
    assert "bring photos to the estimate" in text
    assert "i can book you for the next open slot" in text


def test_features_on_prompt_keeps_the_real_promises():
    text = rendered_prompt(ALL_ON)
    assert "text photos" in text
    assert "15 minutes" in text


# Fla. Stat. 934.03: every greeting says the call may be recorded before asking anything.
NOTICES = {"en": "may be recorded", "es": "puede ser grabada", "fr": "peut être enregistré", "ht": "ka anrejistre"}


def first_messages_in_prompt():
    rows = re.findall(r"^\| \*\*(en|es|fr|ht)\*\*[^|]*\| `([^`]+)` \|$", PROMPT, re.MULTILINE)
    return dict(rows)


def test_prompt_lists_a_first_message_per_language():
    assert set(first_messages_in_prompt()) == set(NOTICES)


@pytest.mark.parametrize("language", sorted(NOTICES))
def test_prompt_first_message_gives_the_recording_notice_first(language):
    message = first_messages_in_prompt()[language]
    question = min(i for i in (message.find("?"), message.find("¿")) if i >= 0)
    assert 0 <= message.lower().find(NOTICES[language]) < question


def test_prompt_repeats_the_recording_notice_on_the_first_language_switch():
    text = PROMPT.lower()
    assert "first time you switch language" in text
    assert "recording notice" in text
    assert "once in the new language" in text


def test_french_first_message_is_french_only():
    assert first_messages_in_prompt()["fr"] == (
        "Bonjour, vous êtes bien chez {{shop_name}}. Je suis {{persona_name}}, l'assistant virtuel. "
        "L'appel peut être enregistré. Comment puis-je vous aider ?"
    )
