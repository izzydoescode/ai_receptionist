from datetime import date

import pytest

from bear.shops import ShopConfig
from test_shop_config import config

TODAY = date(2026, 12, 1)


def shop(**changes):
    return ShopConfig.model_validate(config(**changes))


def test_hours_text_groups_matching_days_and_lists_upcoming_holidays():
    text = shop(holidays=["2026-11-26", "2026-12-25", "2027-01-01"]).hours_text(TODAY)
    assert text == (
        "Monday to Friday 8:00 AM to 6:00 PM; Saturday 9:00 AM to 2:00 PM; closed Sunday. "
        "Closed for holidays on Friday, December 25, 2026 and Friday, January 1, 2027."
    )


def test_hours_text_without_holidays_and_with_split_days():
    week = {
        "monday": [{"open": "08:00", "close": "12:00"}, {"open": "13:00", "close": "17:00"}],
        "tuesday": [],
        "wednesday": [],
        "thursday": [{"open": "08:00", "close": "17:00"}],
        "friday": [{"open": "08:00", "close": "17:00"}],
        "saturday": [{"open": "08:00", "close": "17:00"}],
        "sunday": [],
    }
    assert shop(hours=week, holidays=[]).hours_text(TODAY) == (
        "Monday 8:00 AM to 12:00 PM and 1:00 PM to 5:00 PM; closed Tuesday to Wednesday; "
        "Thursday to Saturday 8:00 AM to 5:00 PM; closed Sunday."
    )


def test_hours_text_caps_the_holiday_list():
    holidays = [f"2026-12-{day:02d}" for day in range(2, 12)]
    assert shop(holidays=holidays).hours_text(TODAY).count("December") == 5


def test_services_text():
    assert shop(services=["oil change"]).services_text() == "oil change"
    assert shop(services=["oil change", "brakes", "tires"]).services_text() == "oil change, brakes, tires"


@pytest.mark.parametrize(
    "features,expected",
    [
        (
            {},
            "We do not send text confirmations. "
            "Tow requests go to staff; there is no automatic dispatch. After logging a tow, say: "
            "\"I've logged it and the shop will call you back as soon as possible. If you're unsafe, call 911.\" "
            "We cannot receive photos by text. Tell callers to bring photos to the estimate, "
            "or the service writer will tell them where to send them.",
        ),
        (
            {"sms_confirmations": True, "tow_dispatch": True, "photo_texting": True},
            "We send a text confirmation after booking. "
            "Tow requests are dispatched automatically; a dispatcher calls back within 15 minutes with an ETA. "
            "Callers can text photos to this number for a faster estimate.",
        ),
    ],
)
def test_features_text_states_only_what_is_on(features, expected):
    assert shop(features=features).features_text() == expected


def test_hours_text_with_one_holiday():
    assert shop(holidays=["2026-12-25"]).hours_text(TODAY).endswith("Closed for holidays on Friday, December 25, 2026.")


@pytest.mark.parametrize(
    "languages,expected",
    [(["en"], "English"), (["es", "en"], "Spanish and English"), (["en", "es", "fr"], "English, Spanish and French")],
)
def test_languages_text(languages, expected):
    data = config(
        languages=languages,
        voices={lang: f"PLACEHOLDER_VOICE_{lang.upper()}" for lang in languages},
        first_messages={
            lang: {"en": "Calls may be recorded.", "es": "Puede ser grabada.", "fr": "Peut être enregistré."}[lang]
            for lang in languages
        },
    )
    assert ShopConfig.model_validate(data).languages_text() == expected


@pytest.mark.parametrize(
    "languages,expected",
    [
        (["en"], "Staff can call back in English."),
        (["en", "es"], "Staff can call back in English and Spanish."),
        (["es", "en", "ht"], "Staff can call back in Spanish, English and Haitian Creole."),
    ],
)
def test_callback_text_names_the_staffed_languages(languages, expected):
    text = shop(callback_languages=languages).callback_text()
    assert text.startswith(expected + " Only promise a callback in one of those languages")


def test_creole_bridge_line_promises_a_creole_callback_only_when_staffed():
    staffed = shop(callback_languages=["en", "ht"]).callback_text()
    unstaffed = shop(callback_languages=["en", "es"]).callback_text()
    assert "yon moun ki pale Kreyòl ap rele w tounen" in staffed
    assert "Kreyòl ap rele" not in unstaffed
    assert "garaj la ap rele w tounen" in unstaffed
