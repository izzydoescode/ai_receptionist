import re
from datetime import time
from pathlib import Path

from bear.shops import load_shops

SHOPS_DIR = Path(__file__).resolve().parents[1] / "shops"


def test_example_shop_loads():
    shops = load_shops(SHOPS_DIR)
    assert list(shops) == ["vice-city-auto"]
    shop = shops["vice-city-auto"]
    assert shop.name == "Vice City Auto"
    assert str(shop.tz) == "America/New_York"
    assert shop.languages == ["en", "es", "fr"]
    assert not any(shop.features.model_dump().values())


def test_example_keeps_the_demo_schedule():
    shop = load_shops(SHOPS_DIR)["vice-city-auto"]
    weekday = [(i.open, i.close) for i in shop.hours.monday]
    assert weekday == [(time(8), time(18))]
    assert all([(i.open, i.close) for i in shop.hours.for_weekday(day)] == weekday for day in range(5))
    assert [(i.open, i.close) for i in shop.hours.saturday] == [(time(9), time(14))]
    assert shop.hours.sunday == []
    assert (shop.bays_per_slot, shop.slot_minutes) == (2, 60)


def test_example_uses_only_fictional_numbers_and_placeholder_voices():
    shop = load_shops(SHOPS_DIR)["vice-city-auto"]
    for number in (shop.twilio_number, shop.transfer_number):
        assert re.fullmatch(r"\+1\d{3}55501\d\d", number)
    assert all(voice.startswith("PLACEHOLDER_") for voice in shop.voices.values())
