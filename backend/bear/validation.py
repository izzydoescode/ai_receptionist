"""Input cleaning shared by the voice tools and the admin forms."""

import re
import unicodedata
from datetime import date

# ZWNJ and ZWJ are format characters but must be kept for Persian (and
# similar scripts) and for emoji sequences.
KEEP_FORMAT = {"‌", "‍"}


def clean_text(value):
    if isinstance(value, str):
        chars = []
        for char in value:
            category = unicodedata.category(char)
            if category == "Cf" and char not in KEEP_FORMAT:
                continue  # ZWSP, bidi controls, BOM
            chars.append(" " if category == "Cc" else char)
        return " ".join("".join(chars).split())
    return value


def integer_to_string(value):
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


def is_nanp(digits):
    # Area code and exchange must be [2-9]XX; this rejects 000-000-0000 and similar fakes.
    return bool(re.fullmatch(r"[2-9]\d{2}[2-9]\d{6}", digits))


# ext, ext., x, #, or "extension", so "…3456 ext 4" is not stored as …34564.
PHONE_EXTENSION = re.compile(r"(?i)(?:\bextension\b|\bext\.|\bext\b|\bx\b|x\s*\d|#)")


def normalize_phone(value):
    value = integer_to_string(value)
    if not isinstance(value, str):
        raise ValueError("invalid phone")
    if PHONE_EXTENSION.search(value):
        raise ValueError("phone extension")
    if len(value) > 32:
        raise ValueError("invalid phone")
    plus = value.strip().startswith("+")
    digits = re.sub(r"\D", "", value)
    # E.164 country codes are 1-9; a leading 0 is not a valid country code.
    if plus and re.fullmatch(r"[1-9]\d{7,14}", digits):
        if digits.startswith("1"):
            # Country code 1 is NANP and must be +1 plus a 10-digit national number.
            if len(digits) != 11 or not is_nanp(digits[1:]):
                raise ValueError("invalid phone")
        return "+" + digits
    if len(digits) == 10 and is_nanp(digits):
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1") and is_nanp(digits[1:]):
        return "+" + digits
    raise ValueError("invalid phone")


def validate_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid date")
    date.fromisoformat(value)
    return value


def lowercase(value):
    return value.lower() if isinstance(value, str) else value
