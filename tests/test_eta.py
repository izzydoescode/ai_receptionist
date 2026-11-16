"""Speakable repair ETAs, counted in the shop's time zone."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from bear.eta import describe_eta, estimate_age, spoken_clock_time, spoken_eta

MIAMI = ZoneInfo("America/New_York")
LOS_ANGELES = ZoneInfo("America/Los_Angeles")
# Monday Nov 16, 2026, 10:00 AM in Miami.
NOW = datetime(2026, 11, 16, 10, 0, tzinfo=MIAMI)


def local(day, hour, minute=0, tz=MIAMI):
    return datetime(2026, 11, day, hour, minute, tzinfo=tz)


@pytest.mark.parametrize(
    "moment,spoken",
    [(local(16, 15), "3 PM"), (local(16, 15, 30), "3:30 PM"), (local(16, 12), "12 PM"), (local(16, 9, 5), "9:05 AM")],
)
def test_clock_time_is_said_the_way_people_say_it(moment, spoken):
    assert spoken_clock_time(moment) == spoken


@pytest.mark.parametrize(
    "eta_at,spoken",
    [
        (local(16, 15), "today by 3 PM"),
        (local(17, 15), "tomorrow by 3 PM"),
        (local(19, 17), "Thursday by 5 PM"),
        (local(22, 17), "Sunday by 5 PM"),
        (local(23, 10), "Monday, November 23 by 10 AM"),
        (local(15, 17), "yesterday by 5 PM"),
        (local(12, 17), "Thursday, November 12 by 5 PM"),
    ],
)
def test_spoken_eta_is_relative_to_the_shop_day(eta_at, spoken):
    assert spoken_eta(eta_at, NOW, MIAMI) == spoken


def test_midnight_is_counted_in_the_shop_time_zone_not_utc():
    # 11:30 PM Monday in Miami is already Tuesday in UTC; the caller should still hear "today".
    late_monday = datetime(2026, 11, 17, 4, 30, tzinfo=UTC)
    assert spoken_eta(late_monday, NOW, MIAMI) == "today by 11:30 PM"
    # Same instant for a Los Angeles shop is 8:30 PM Monday.
    assert spoken_eta(late_monday, NOW, LOS_ANGELES) == "today by 8:30 PM"


@pytest.mark.parametrize(
    "updated,age", [(local(16, 8), "today"), (local(15, 18), "yesterday"), (local(13, 9), "3 days ago")]
)
def test_estimate_age(updated, age):
    assert estimate_age(updated, NOW, MIAMI) == age


def describe(**overrides):
    fields = {
        "status": "in_progress",
        "eta_at": local(17, 15),
        "eta_note": "",
        "updated_at": local(16, 8),
        "now": NOW,
        "shop_tz": MIAMI,
    }
    fields.update(overrides)
    return describe_eta(**fields)


def test_future_eta_has_no_agent_hint():
    assert describe() == {"eta": "tomorrow by 3 PM", "eta_past_due": False, "estimate_updated": "today"}


def test_note_is_added_after_the_spoken_time():
    assert describe(eta_note="waiting on the paint to cure")["eta"] == "tomorrow by 3 PM (waiting on the paint to cure)"


def test_past_due_eta_tells_the_agent_not_to_guess():
    result = describe(eta_at=local(15, 17))
    assert result["eta_past_due"] is True
    assert "yesterday by 5 PM" in result["message"]
    assert "Don't guess" in result["message"]
    assert "take a message" in result["message"]


def test_missing_eta_tells_the_agent_not_to_guess():
    result = describe(eta_at=None)
    assert result["eta"] == ""
    assert "hasn't set a finish time" in result["message"]


def test_free_text_eta_without_a_time_is_passed_through():
    result = describe(eta_at=None, eta_note="1 day after approval")
    assert result == {"eta": "1 day after approval", "eta_past_due": False, "estimate_updated": "today"}


def test_ready_cars_are_never_past_due():
    result = describe(status="ready_for_pickup", eta_at=local(12, 17))
    assert result == {"eta": "ready now", "eta_past_due": False, "estimate_updated": "today"}
