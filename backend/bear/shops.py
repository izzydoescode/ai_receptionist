"""Per-shop configuration: one validated YAML file per shop."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, time
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
