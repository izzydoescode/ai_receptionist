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
