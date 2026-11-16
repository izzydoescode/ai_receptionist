"""Staff alerts for bookings, messages and tows.

ALERTS_MODE=log writes one line per event that carries only the kind, the shop
and the ticket id. Names, phones and free text (reasons, tow locations) stay out
of the logs. Real delivery (email, SMS) hooks in behind ALERTS_MODE later.
An alert must never change a tool result: `send_staff_alert` swallows and logs
every error, because the row is already committed when it runs.
"""

import logging

PROD_LOG_WARNING = (
    "ALERTS_MODE=log: staff alerts are log-only in production; nothing is actually being delivered by email or SMS"
)
LOG_ONLY_HEADER = "STAFF ALERT (log only, not delivered)"
MODES = frozenset({"log", "off"})
KINDS = frozenset({"booking", "message", "tow"})

alerts_logger = logging.getLogger("bear.alerts")


def log_only_line(kind, shop_id, public_id):
    if kind not in KINDS:
        raise ValueError(f"unknown alert kind {kind}")
    return f"{LOG_ONLY_HEADER}: new {kind} for shop {shop_id}, ticket {public_id}"


def send_staff_alert(mode, kind, *, shop_id, public_id):
    """Notify staff about a saved booking, message or tow. Never raises."""
    try:
        if mode == "log":
            alerts_logger.info(log_only_line(kind, shop_id, public_id))
    except Exception:
        alerts_logger.exception("staff alert failed for %s %s", kind, public_id)


def warn_if_prod_log_only(mode, env):
    if env == "prod" and mode == "log":
        logging.getLogger("bear").warning(PROD_LOG_WARNING)
