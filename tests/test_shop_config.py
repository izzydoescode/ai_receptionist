import copy

import pytest
from pydantic import ValidationError

from bear.shops import ShopConfig

WEEKDAY = [{"open": "08:00", "close": "18:00"}]
VALID = {
    "shop_id": "demo-shop",
    "name": "Demo Shop",
    "city": "Miami",
    "address": "100 Demo Street, Miami, FL 33101",
    "timezone": "America/New_York",
    "twilio_number": "+13055550100",
    "transfer_number": "+13055550142",
    "hours": {
        "monday": WEEKDAY,
        "tuesday": WEEKDAY,
        "wednesday": WEEKDAY,
        "thursday": WEEKDAY,
        "friday": WEEKDAY,
        "saturday": [{"open": "09:00", "close": "14:00"}],
        "sunday": [],
    },
    "holidays": ["2026-12-25"],
    "bays_per_slot": 2,
    "slot_minutes": 60,
    "services": ["oil change", "brakes"],
    "languages": ["en", "es"],
    "ht_fallback": "message",
    "voices": {"en": "PLACEHOLDER_VOICE_EN", "es": "PLACEHOLDER_VOICE_ES"},
    "first_messages": {
        "en": "Demo Shop, this is Bear. Calls may be recorded. What can I do for you?",
        "es": "Gracias por llamar a Demo Shop. La llamada puede ser grabada. ¿En qué le puedo ayudar?",
    },
    "features": {"sms_confirmations": False, "tow_dispatch": False, "photo_texting": False},
    "persona": {"name": "Bear", "style": "friendly mechanic"},
}


def config(**changes):
    data = copy.deepcopy(VALID)
    for dotted, value in changes.items():
        target = data
        *parents, last = dotted.split("__")
        for key in parents:
            target = target[key]
        if value is ...:
            del target[last]
        else:
            target[last] = value
    return data


def error_text(data):
    with pytest.raises(ValidationError) as error:
        ShopConfig.model_validate(data)
    return str(error.value)


def test_valid_config_parses_typed_values():
    shop = ShopConfig.model_validate(VALID)
    assert shop.shop_id == "demo-shop"
    assert str(shop.tz) == "America/New_York"
    assert shop.hours.for_weekday(0)[0].open.hour == 8
    assert shop.hours.for_weekday(6) == []
    assert [d.isoformat() for d in shop.holidays] == ["2026-12-25"]
    assert shop.features.sms_confirmations is False
    assert shop.persona.name == "Bear"


def test_optional_fields_have_safe_defaults():
    shop = ShopConfig.model_validate(config(holidays=..., features=..., ht_fallback=..., persona__style=...))
    assert shop.holidays == []
    assert shop.ht_fallback == "message"
    assert shop.persona.style is None
    assert not any(shop.features.model_dump().values())


@pytest.mark.parametrize("field", ["twilio_number", "transfer_number"])
@pytest.mark.parametrize(
    "value", ["3055550100", "+1 305 555 0100", "+0305555010", "+1", "tel:+13055550100", 13055550100]
)
def test_bad_phone_is_rejected_readably(field, value):
    text = error_text(config(**{field: value}))
    assert field in text
    assert "E.164" in text


def test_close_before_open_is_rejected():
    text = error_text(config(hours__monday=[{"open": "18:00", "close": "08:00"}]))
    assert "close must be after open" in text


def test_overlapping_intervals_are_rejected():
    hours = [{"open": "08:00", "close": "12:00"}, {"open": "11:00", "close": "17:00"}]
    assert "overlap" in error_text(config(hours__tuesday=hours))


def test_split_day_is_allowed():
    hours = [{"open": "08:00", "close": "12:00"}, {"open": "13:00", "close": "17:00"}]
    assert len(ShopConfig.model_validate(config(hours__tuesday=hours)).hours.tuesday) == 2


@pytest.mark.parametrize("value", [480, "8:00", "8am", "24:00", None])
def test_times_must_be_quoted_hh_mm(value):
    assert "HH:MM" in error_text(config(hours__monday=[{"open": value, "close": "18:00"}]))


def test_every_weekday_must_be_listed():
    assert "sunday" in error_text(config(hours__sunday=...))


@pytest.mark.parametrize(
    "language,message",
    [
        ("en", "Demo Shop, this is Bear. What can I do for you?"),
        ("es", "Gracias por llamar a Demo Shop. ¿En qué le puedo ayudar?"),
    ],
)
def test_first_message_needs_recording_notice(language, message):
    text = error_text(config(**{f"first_messages__{language}": message}))
    assert f"first_messages.{language}" in text
    assert "recording notice" in text


def test_recording_notice_match_ignores_case_and_accents():
    data = config(
        languages=["fr"],
        voices={"fr": "PLACEHOLDER_VOICE_FR"},
        first_messages={"fr": "Bonjour. CET APPEL PEUT ETRE ENREGISTRE."},
    )
    assert ShopConfig.model_validate(data).first_messages["fr"].startswith("Bonjour")


@pytest.mark.parametrize(
    "message",
    [
        "Gracias por llamar a Demo Shop. Las llamadas pueden ser grabadas. ¿En qué le puedo ayudar?",
        "Gracias por llamar a Demo Shop. Esta llamada se puede grabar. ¿En qué le puedo ayudar?",
    ],
)
def test_spanish_recording_notice_accepts_natural_variants(message):
    data = config(languages=["es"], voices={"es": "PLACEHOLDER_VOICE_ES"}, first_messages={"es": message})
    assert ShopConfig.model_validate(data).first_messages["es"] == message


@pytest.mark.parametrize("field", ["voices", "first_messages"])
def test_each_language_needs_voice_and_first_message(field):
    data = config()
    del data[field]["es"]
    text = error_text(data)
    assert field in text
    assert "es" in text


@pytest.mark.parametrize("field", ["voices", "first_messages"])
def test_unlisted_language_entries_are_rejected(field):
    data = config(languages=["en"])
    other = "first_messages" if field == "voices" else "voices"
    del data[other]["es"]
    text = error_text(data)
    assert f"{field}: es not in languages" in text


@pytest.mark.parametrize("languages", [[], ["en", "en"], ["en", "ht"], ["en", "de"]])
def test_languages_must_be_unique_subset(languages):
    error_text(config(languages=languages))


def test_french_fallback_needs_french():
    assert "ht_fallback" in error_text(config(ht_fallback="french"))


def test_french_fallback_with_french_is_valid():
    data = config(
        ht_fallback="french",
        languages=["en", "fr"],
        voices={"en": "PLACEHOLDER_VOICE_EN", "fr": "PLACEHOLDER_VOICE_FR"},
        first_messages={"en": VALID["first_messages"]["en"], "fr": "Cet appel peut être enregistré."},
    )
    assert ShopConfig.model_validate(data).ht_fallback == "french"


@pytest.mark.parametrize(
    "changes",
    [
        {"shop_id": "Bad Shop"},
        {"shop_id": "-demo"},
        {"timezone": "Mars/Olympus"},
        {"bays_per_slot": 0},
        {"slot_minutes": 0},
        {"services": []},
        {"services": [""]},
        {"name": ""},
        {"persona__name": ""},
        {"holidays": ["not-a-date"]},
        {"ht_fallback": "creole"},
        {"voices__en": "bad voice id!"},
        {"features__sms_confirmations": "maybe"},
        {"unknown_key": 1},
        {"features__teleport": True},
    ],
)
def test_invalid_values_are_rejected(changes):
    error_text(config(**changes))


def test_twilio_and_transfer_numbers_must_differ():
    assert "transfer_number" in error_text(config(transfer_number=VALID["twilio_number"]))


def test_config_is_immutable():
    shop = ShopConfig.model_validate(VALID)
    with pytest.raises(ValidationError):
        shop.name = "Other"
