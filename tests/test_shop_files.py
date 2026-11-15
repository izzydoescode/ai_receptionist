"""Every shop file in shops/ greets callers with the recording notice before asking anything."""

from pathlib import Path

import pytest
import yaml

from bear.shops import RECORDING_NOTICES, fold

SHOP_FILES = sorted((Path(__file__).resolve().parents[1] / "shops").glob("*.yaml"))
GREETINGS = [
    (path.name, language, message)
    for path in SHOP_FILES
    for language, message in yaml.safe_load(path.read_text("utf-8"))["first_messages"].items()
]


def test_there_are_shop_files():
    assert SHOP_FILES


@pytest.mark.parametrize("file,language,message", GREETINGS)
def test_greeting_gives_the_recording_notice_before_the_question(file, language, message):
    question = min(i for i in (message.find("?"), message.find("¿")) if i >= 0)
    assert 0 <= fold(message).find(fold(RECORDING_NOTICES[language])) < question


AI_TELLS = (
    "virtual assistant",
    "asistente virtual",
    "assistant virtuel",
    "asistan vityèl",
)


@pytest.mark.parametrize("file,language,message", GREETINGS)
def test_greeting_does_not_volunteer_that_bear_is_ai(file, language, message):
    lower = message.lower()
    for phrase in AI_TELLS:
        assert phrase not in lower
