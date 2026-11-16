"""Turn a repair order's finish time into words Bear can say on the phone.

The backend speaks English; the agent translates when the call is in Spanish or French.
Days are counted in the shop's time zone, so "tomorrow" means the shop's tomorrow.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

DAYS_SPOKEN_BY_WEEKDAY = 6  # Within this many days, "Thursday" is clear; further out, add the date.


def spoken_clock_time(moment: datetime) -> str:
    """3 PM, 3:30 PM, 12 PM."""
    hour = moment.strftime("%I").lstrip("0")
    minutes = f":{moment.minute:02d}" if moment.minute else ""
    return f"{hour}{minutes} {moment.strftime('%p')}"


def spoken_day(day_offset: int, moment: datetime) -> str:
    if day_offset == 0:
        return "today"
    if day_offset == 1:
        return "tomorrow"
    if day_offset == -1:
        return "yesterday"
    if 1 < day_offset <= DAYS_SPOKEN_BY_WEEKDAY:
        return moment.strftime("%A")
    return f"{moment.strftime('%A, %B')} {moment.day}"


def spoken_eta(eta_at: datetime, now: datetime, shop_tz: ZoneInfo) -> str:
    """'tomorrow by 3 PM', 'Thursday by 5 PM', 'Monday, November 30 by 10 AM'."""
    local_eta = eta_at.astimezone(shop_tz)
    day_offset = (local_eta.date() - now.astimezone(shop_tz).date()).days
    return f"{spoken_day(day_offset, local_eta)} by {spoken_clock_time(local_eta)}"


def estimate_age(updated_at: datetime, now: datetime, shop_tz: ZoneInfo) -> str:
    """How long ago staff last touched the estimate: 'today', 'yesterday', '3 days ago'."""
    days_ago = (now.astimezone(shop_tz).date() - updated_at.astimezone(shop_tz).date()).days
    if days_ago <= 0:
        return "today"
    if days_ago == 1:
        return "yesterday"
    return f"{days_ago} days ago"


def describe_eta(
    *,
    status: str,
    eta_at: datetime | None,
    eta_note: str,
    updated_at: datetime,
    now: datetime,
    shop_tz: ZoneInfo,
) -> dict:
    """Fields for check_repair_status: what to say about the finish time, plus a hint for the agent."""
    details = {
        "eta": eta_note,
        "eta_past_due": False,
        "estimate_updated": estimate_age(updated_at, now, shop_tz),
    }
    if status == "ready_for_pickup":
        details["eta"] = eta_note or "ready now"
        return details
    if eta_at is None:
        if not eta_note:
            details["message"] = (
                "The shop hasn't set a finish time yet. Don't guess one: offer to take a message "
                "so the service writer can call with an estimate."
            )
        return details
    spoken = spoken_eta(eta_at, now, shop_tz)
    details["eta"] = f"{spoken} ({eta_note})" if eta_note else spoken
    if eta_at < now:
        details["eta_past_due"] = True
        details["message"] = (
            f"The estimate was {spoken}, and the car isn't done yet. Don't guess a new time: say the job is "
            "running past the estimate and offer to take a message so the service writer can call with an update."
        )
    return details
