from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from bear.shops import ShopConfig
from test_shop_config import config

NY = ZoneInfo("America/New_York")
# 2026-12-21 is a Monday; 2026-12-25 (a Friday) is a configured holiday.
MONDAY = datetime(2026, 12, 21, tzinfo=NY)


def shop(**changes):
    return ShopConfig.model_validate(config(**changes))


def at(day_offset, hour, minute=0):
    return MONDAY.replace(day=MONDAY.day + day_offset, hour=hour, minute=minute)


@pytest.mark.parametrize(
    "when,expected",
    [
        (at(0, 8), at(0, 8)),
        (at(0, 10, 30), at(0, 10)),
        (at(0, 17, 59), at(0, 17)),
        (at(5, 13), at(5, 13)),
    ],
)
def test_slot_start_floors_to_the_slot_grid(when, expected):
    assert shop().slot_start(when) == expected


@pytest.mark.parametrize("when", [at(0, 7, 59), at(0, 18), at(5, 14), at(6, 10), at(4, 10)])
def test_no_slot_outside_hours_on_closed_days_and_holidays(when):
    assert shop().slot_start(when) is None


def test_slot_start_accepts_other_timezones():
    assert shop().slot_start(at(0, 10).astimezone(UTC)) == at(0, 10)


def test_slot_must_finish_before_closing():
    custom = shop(slot_minutes=90, hours__monday=[{"open": "08:00", "close": "10:00"}])
    assert custom.slot_start(at(0, 8, 45)) == at(0, 8)
    assert custom.slot_start(at(0, 9, 45)) is None  # 9:30 slot would run past 10:00
    assert list(custom.upcoming_slots(at(0, 0), days=1)) == [at(0, 8)]


def test_split_day_uses_each_interval_grid():
    custom = shop(
        slot_minutes=30, hours__monday=[{"open": "08:15", "close": "12:00"}, {"open": "13:00", "close": "15:00"}]
    )
    assert custom.slot_start(at(0, 8, 50)) == at(0, 8, 45)
    assert custom.slot_start(at(0, 12, 30)) is None
    assert custom.slot_start(at(0, 14, 59)) == at(0, 14, 30)


@pytest.mark.parametrize(
    "when,message",
    [
        (at(4, 10), "We're closed that day for a holiday."),
        (at(6, 10), "We're closed Sundays."),
        (at(0, 7), "That day we take drop-offs from 8:00 AM to 6:00 PM."),
        (at(5, 15), "That day we take drop-offs from 9:00 AM to 2:00 PM."),
        (at(0, 10), None),
    ],
)
def test_closed_reason_is_speakable(when, message):
    assert shop().closed_reason(when) == message


def test_closed_reason_lists_split_hours():
    custom = shop(hours__monday=[{"open": "08:00", "close": "12:00"}, {"open": "13:30", "close": "17:00"}])
    assert custom.closed_reason(at(0, 12, 30)) == (
        "That day we take drop-offs from 8:00 AM to 12:00 PM or from 1:30 PM to 5:00 PM."
    )


def test_upcoming_slots_skip_closed_days_holidays_and_the_current_slot():
    starts = shop().upcoming_slots(at(3, 17))
    assert next(starts) == at(5, 9)  # Friday is a holiday; Saturday opens at 9
    assert [next(starts) for _ in range(4)] == [at(5, 10), at(5, 11), at(5, 12), at(5, 13)]
    assert next(starts) == at(7, 8)  # Sunday closed


def test_upcoming_slots_stop_after_the_window():
    closed = {day: [] for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
    assert list(shop(hours=closed).upcoming_slots(at(0, 9), days=14)) == []
    assert len(list(shop().upcoming_slots(at(0, 0), days=1))) == 10
