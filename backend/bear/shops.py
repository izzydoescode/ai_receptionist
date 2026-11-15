"""Per-shop configuration: one validated YAML file per shop."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

SHOP_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# Fla. Stat. 934.03 needs every party's consent, so each greeting must say the call may be recorded.
RECORDING_NOTICES = {"en": "may be recorded", "es": "puede ser grabada", "fr": "peut être enregistré"}


def fold(value: str) -> str:
    """Lowercase and strip accents so notice checks ignore case and diacritics."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def parse_e164(value):
    if not isinstance(value, str) or not re.fullmatch(r"\+[1-9]\d{7,14}", value):
        raise ValueError("must be an E.164 number like +13055550100")
    return value


def parse_clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        # Unquoted YAML times such as 18:00 load as integers, so insist on quoted strings.
        raise ValueError('must be a quoted 24-hour time "HH:MM", like "08:00"')
    return time.fromisoformat(value)


E164 = Annotated[str, BeforeValidator(parse_e164)]
Clock = Annotated[time, BeforeValidator(parse_clock)]
Label = Annotated[str, Field(min_length=1, max_length=80)]
Language = Literal["en", "es", "fr"]
VoiceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OpenInterval(_Frozen):
    open: Clock
    close: Clock

    @model_validator(mode="after")
    def _close_after_open(self):
        if self.close <= self.open:
            raise ValueError("close must be after open")
        return self


class WeeklyHours(_Frozen):
    """Open intervals per weekday; an empty list means closed all day."""

    monday: list[OpenInterval]
    tuesday: list[OpenInterval]
    wednesday: list[OpenInterval]
    thursday: list[OpenInterval]
    friday: list[OpenInterval]
    saturday: list[OpenInterval]
    sunday: list[OpenInterval]

    @model_validator(mode="after")
    def _ordered_without_overlap(self):
        for day in WEEKDAYS:
            intervals = getattr(self, day)
            for before, after in zip(intervals, intervals[1:], strict=False):
                if after.open < before.close:
                    raise ValueError(f"{day} intervals overlap or are out of order")
        return self

    def for_weekday(self, weekday: int) -> list[OpenInterval]:
        return getattr(self, WEEKDAYS[weekday])


class Features(_Frozen):
    sms_confirmations: bool = False
    tow_dispatch: bool = False
    photo_texting: bool = False


class Persona(_Frozen):
    name: Label
    style: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class ShopConfig(_Frozen):
    shop_id: Annotated[str, Field(pattern=SHOP_ID_PATTERN)]
    name: Label
    city: Label
    address: Annotated[str, Field(min_length=1, max_length=200)]
    timezone: str
    twilio_number: E164
    transfer_number: E164
    hours: WeeklyHours
    holidays: list[date] = []
    bays_per_slot: int = Field(ge=1, le=50)
    slot_minutes: int = Field(ge=5, le=480)
    services: list[Label] = Field(min_length=1)
    languages: list[Language] = Field(min_length=1)
    ht_fallback: Literal["message", "french"] = "message"
    voices: dict[Language, VoiceId]
    first_messages: dict[Language, Annotated[str, Field(min_length=1, max_length=500)]]
    features: Features = Features()
    persona: Persona

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("must be an IANA time zone like America/New_York") from None
        return value

    @field_validator("languages")
    @classmethod
    def _unique_languages(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("languages must not repeat")
        return value

    @model_validator(mode="after")
    def _consistent_languages(self):
        if self.transfer_number == self.twilio_number:
            raise ValueError("transfer_number must differ from twilio_number")
        if self.ht_fallback == "french" and "fr" not in self.languages:
            raise ValueError("ht_fallback: french needs fr in languages")
        for field in ("voices", "first_messages"):
            entries = getattr(self, field)
            missing = [lang for lang in self.languages if lang not in entries]
            if missing:
                raise ValueError(f"{field}: missing an entry for {', '.join(missing)}")
            extra = [lang for lang in entries if lang not in self.languages]
            if extra:
                raise ValueError(f"{field}: {', '.join(extra)} not in languages")
        for lang, message in self.first_messages.items():
            notice = RECORDING_NOTICES[lang]
            if fold(notice) not in fold(message):
                raise ValueError(f'first_messages.{lang} must include the recording notice "{notice}"')
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def _day_slots(self, day: date) -> Iterator[tuple[datetime, datetime]]:
        """Yield (interval_open, interval_close) as aware local datetimes for an open day."""
        if day in self.holidays:
            return
        for interval in self.hours.for_weekday(day.weekday()):
            yield (
                datetime.combine(day, interval.open, self.tz),
                datetime.combine(day, interval.close, self.tz),
            )

    def slot_start(self, when: datetime) -> datetime | None:
        """Return the start of the bookable slot containing ``when``, or None if there is none."""
        local = when.astimezone(self.tz)
        step = timedelta(minutes=self.slot_minutes)
        for opening, closing in self._day_slots(local.date()):
            if opening <= local < closing:
                start = opening + (local - opening) // step * step
                return start if start + step <= closing else None
        return None

    def closed_reason(self, when: datetime) -> str | None:
        """Speakable reason ``when`` is not bookable, or None when it falls in a slot."""
        local = when.astimezone(self.tz)
        if self.slot_start(local) is not None:
            return None
        if local.date() in self.holidays:
            return "We're closed that day for a holiday."
        intervals = self.hours.for_weekday(local.weekday())
        if not intervals:
            return f"We're closed {WEEKDAYS[local.weekday()].capitalize()}s."
        spans = " or ".join(f"from {speak_time(i.open)} to {speak_time(i.close)}" for i in intervals)
        return f"That day we take drop-offs {spans}."

    def upcoming_slots(self, after: datetime, days: int = 14) -> Iterator[datetime]:
        """Yield slot starts strictly after ``after`` for ``days`` local calendar days, in order."""
        local = after.astimezone(self.tz)
        step = timedelta(minutes=self.slot_minutes)
        for offset in range(days):
            for opening, closing in self._day_slots(local.date() + timedelta(days=offset)):
                start = opening
                while start + step <= closing:
                    if start > local:
                        yield start
                    start += step


def speak_time(value: time) -> str:
    """Format 13:30 as "1:30 PM" for the voice agent."""
    return f"{value.hour % 12 or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"
