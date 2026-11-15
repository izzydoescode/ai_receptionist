"""Per-shop configuration: one validated YAML file per shop."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator, model_validator

SHOP_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
# Calls to a number no shop owns run as this pseudo-shop, which may only take messages.
UNKNOWN_SHOP_ID = "unknown"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# Fla. Stat. 934.03 needs every party's consent, so each greeting must say the call may be recorded.
LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "ht": "Haitian Creole"}
RECORDING_NOTICES = {"en": "may be recorded", "es": "puede ser grabada", "fr": "peut être enregistré"}
# What the agent says instead of promising a feature the shop has turned off (PLAN Task 10).
TOW_FALLBACK_REPLY = (
    "I've logged the tow, and the shop will call you back as soon as possible. If you're unsafe, call 911."
)
PHOTO_FALLBACK_ADVICE = (
    "Tell callers to bring photos to the estimate, or the service writer will tell them where to send them."
)


# What the agent says, slowly, to a caller who speaks Kreyòl (see docs/TRANSLATION_REVIEW.md).
# It promises a Kreyòl-speaking callback only when the shop lists ht in callback_languages.
KREYOL_BRIDGE_STAFFED = (
    "Bonjou! Mwen pa pale Kreyòl byen. Nou ka pale franse, anglè oswa panyòl, "
    "oswa mwen pran mesaj ou epi yon moun ki pale Kreyòl ap rele w tounen."
)
KREYOL_BRIDGE_UNSTAFFED = (
    "Bonjou! Mwen pa pale Kreyòl byen. Nou ka pale franse, anglè oswa panyòl, "
    "oswa mwen pran mesaj ou epi garaj la ap rele w tounen."
)


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def callback_text(languages) -> str:
    """Tell the agent which callback languages it may promise, plus the matching Kreyòl bridge line."""
    bridge = KREYOL_BRIDGE_STAFFED if "ht" in languages else KREYOL_BRIDGE_UNSTAFFED
    return (
        f"Staff can call back in {join_names([LANGUAGE_NAMES[lang] for lang in languages])}. "
        "Only promise a callback in one of those languages; otherwise say the shop will call back "
        f'as soon as possible, without promising a language. Kreyòl bridge line: "{bridge}"'
    )


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
# Languages staff can return calls in; Kreyòl is possible here even though the voice agent can't speak it.
CallbackLanguage = Literal["en", "es", "fr", "ht"]
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
    callback_languages: list[CallbackLanguage] = Field(default=["en"], min_length=1)
    voices: dict[Language, VoiceId]
    first_messages: dict[Language, Annotated[str, Field(min_length=1, max_length=500)]]
    features: Features = Features()
    persona: Persona

    @field_validator("shop_id")
    @classmethod
    def _not_reserved(cls, value):
        if value == UNKNOWN_SHOP_ID:
            raise ValueError(f"{UNKNOWN_SHOP_ID} is reserved for calls to unrecognized numbers")
        return value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("must be an IANA time zone like America/New_York") from None
        return value

    @field_validator("languages", "callback_languages")
    @classmethod
    def _unique_languages(cls, value, info):
        if len(set(value)) != len(value):
            raise ValueError(f"{info.field_name} must not repeat")
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
            return "We're closed that day for a holiday, but I can look at another day for you."
        intervals = self.hours.for_weekday(local.weekday())
        if not intervals:
            return f"We're closed {WEEKDAYS[local.weekday()].capitalize()}s, but I can look at another day for you."
        spans = " or ".join(f"from {speak_time(i.open)} to {speak_time(i.close)}" for i in intervals)
        return f"We're not open at that time. That day we take drop-offs {spans}."

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

    def hours_text(self, today: date, max_holidays: int = 5) -> str:
        """Weekly hours with matching consecutive days grouped, plus upcoming holidays."""
        groups: list[tuple[str, int, int]] = []
        for weekday in range(7):
            spans = " and ".join(
                f"{speak_time(i.open)} to {speak_time(i.close)}" for i in self.hours.for_weekday(weekday)
            )
            if groups and groups[-1][0] == spans:
                groups[-1] = (spans, groups[-1][1], weekday)
            else:
                groups.append((spans, weekday, weekday))
        parts = []
        for spans, first, last in groups:
            days = WEEKDAYS[first].capitalize()
            if last != first:
                days += f" to {WEEKDAYS[last].capitalize()}"
            parts.append(f"{days} {spans}" if spans else f"closed {days}")
        if groups and not groups[0][0]:
            text = "We're " + "; ".join(parts) + "."
        else:
            text = "We're open " + "; ".join(parts) + "."
        upcoming = sorted(day for day in self.holidays if day >= today)[:max_holidays]
        if upcoming:
            names = [f"{d:%A}, {d:%B} {d.day}, {d.year}" for d in upcoming]
            listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            text += f" We'll also be closed for holidays on {listed}."
        return text

    def languages_text(self) -> str:
        return join_names([LANGUAGE_NAMES[lang] for lang in self.languages])

    def callback_text(self) -> str:
        return callback_text(self.callback_languages)

    def services_text(self) -> str:
        return ", ".join(self.services)

    def features_text(self) -> str:
        """Tell the agent what the shop can and cannot do, so it never promises a missing feature."""
        f = self.features
        return " ".join(
            [
                "We send a text confirmation after booking."
                if f.sms_confirmations
                else "We do not send text confirmations.",
                "Tow requests get dispatched automatically, and a dispatcher will call back within 15 minutes with an arrival time."
                if f.tow_dispatch
                else "Tow requests go to staff; there's no automatic dispatch. After logging a tow, say: "
                f'"{TOW_FALLBACK_REPLY}"',
                "Callers can text photos to this number for a faster estimate."
                if f.photo_texting
                else f"We cannot receive photos by text. {PHOTO_FALLBACK_ADVICE}",
            ]
        )


def speak_time(value: time) -> str:
    """Format 13:30 as "1:30 PM" for the voice agent."""
    return f"{value.hour % 12 or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


class ShopConfigError(RuntimeError):
    """One or more shop files are missing or invalid; the message lists every problem."""


def _location(loc) -> str:
    path = ""
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else f".{part}" if path else str(part)
    return path


def _problems(name: str, error: ValidationError) -> list[str]:
    # Only locations and messages: input values never reach logs or exceptions.
    problems = []
    for item in error.errors(include_input=False, include_url=False):
        message = item["msg"].removeprefix("Value error, ")
        where = _location(item["loc"])
        problems.append(f"{name}: {where}: {message}" if where else f"{name}: {message}")
    return problems


def load_shops(directory: str | Path) -> dict[str, ShopConfig]:
    """Load and validate every ``*.yaml`` file in ``directory``, failing on the first bad config set."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ShopConfigError(f"SHOPS_DIR {directory} is not a directory")
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise ShopConfigError(f"SHOPS_DIR {directory} has no shop files (*.yaml)")

    shops: dict[str, ShopConfig] = {}
    numbers: dict[str, str] = {}
    problems: list[str] = []
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            line = f" (line {mark.line + 1})" if mark is not None else ""
            problems.append(f"{path.name}: invalid YAML{line}")
            continue
        if not isinstance(data, dict):
            problems.append(f"{path.name}: must be a mapping of settings")
            continue
        try:
            shop = ShopConfig.model_validate(data)
        except ValidationError as error:
            problems.extend(_problems(path.name, error))
            continue
        if shop.shop_id != path.stem:
            problems.append(f"{path.name}: shop_id {shop.shop_id} must match the file name")
            continue
        if shop.twilio_number in numbers:
            problems.append(
                f"twilio_number {shop.twilio_number} is used by both {numbers[shop.twilio_number]} and {path.name}"
            )
            continue
        numbers[shop.twilio_number] = path.name
        shops[shop.shop_id] = shop
    if problems:
        raise ShopConfigError(f"Invalid shop config in {directory}:\n" + "\n".join(f"- {p}" for p in problems))
    return shops
