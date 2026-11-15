"""Log-only staff alerts. Real email and SMS can hook in behind ALERTS_MODE later."""

import logging
import re

PROD_LOG_WARNING = (
    "ALERTS_MODE=log: staff alerts are log-only in production; nothing is actually being delivered by email or SMS"
)
BORDER = "=" * 72
MODES = frozenset({"log", "off"})


def mask_phone(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    return f"**-***-{digits[-4:]}" if len(digits) >= 4 else "**-***-????"


def _who(name, phone):
    return f"{name} ({mask_phone(phone)})"


def event_sentence(kind, **fields):
    who = _who(fields["name"], fields["phone"])
    if kind == "booking":
        return f"{who} booked the {fields['vehicle']} for {fields['service']} on {fields['when']}."
    if kind == "message":
        return f"{who} left a message ({fields.get('urgency') or 'normal'}): {fields['reason']}."
    if kind == "tow":
        return f"{who} requested a tow from {fields['pickup_location']} ({fields['issue']})."
    raise ValueError(f"unknown alert kind {kind}")


def _header(channel, env):
    title = "EMAIL SENT!" if channel == "email" else "TEXT SENT!"
    if env in {"dev", "staging"}:
        noun = "email" if channel == "email" else "text"
        return f"{title} (simulated; no real {noun} went out)"
    return title


def _block(channel, env, body):
    return "\n".join((BORDER, _header(channel, env), BORDER, *body, BORDER))


def log_staff_alert(mode, env, kind, **fields):
    if mode != "log":
        return
    sentence = event_sentence(kind, **fields)
    logger = logging.getLogger("bear.alerts")
    logger.info(
        _block(
            "email",
            env,
            ("Dear owner,", "", sentence, "", "Your virtual receptionist, Bear."),
        )
    )
    logger.info(_block("text", env, (sentence,)))


def warn_if_prod_log_only(mode, env):
    if env == "prod" and mode == "log":
        logging.getLogger("bear").warning(PROD_LOG_WARNING)
