"""Staff alerts for bookings, messages and tows.

ALERTS_MODE:
- off: nothing.
- log: one line per event with only the kind, the shop and the ticket id. Nothing is delivered.
- email: the same line, plus an email to the shop's `alerts.email` recipients through the Resend
  HTTP API. The email carries the details staff need to call back (name, phone, reason); the
  logs never do.

An alert must never change a tool result. The row is already committed when an alert runs, so
`StaffAlerter.send` swallows every error, and email goes out on a small background thread pool
so a slow email API never counts against the tool deadline or delays the caller.
SMS to the owner comes later, behind A2P 10DLC approval.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

PROD_LOG_WARNING = (
    "ALERTS_MODE=log: staff alerts are log-only in production; nothing is actually being delivered by email or SMS"
)
LOG_ONLY_HEADER = "STAFF ALERT (log only, not delivered)"
EMAIL_HEADER = "STAFF ALERT (email queued)"
MODES = frozenset({"log", "off", "email"})
KINDS = frozenset({"booking", "message", "tow"})
RESEND_URL = "https://api.resend.com/emails"
EMAIL_TIMEOUT_SECONDS = 10

alerts_logger = logging.getLogger("bear.alerts")

KIND_TITLES = {"booking": "New booking", "message": "New message", "tow": "Tow request"}
DETAIL_LABELS = {
    "name": "Name",
    "phone": "Phone",
    "vehicle": "Vehicle",
    "service": "Service",
    "when": "When",
    "notes": "Notes",
    "reason": "Reason",
    "urgency": "Urgency",
    "callback_language": "Call back in",
    "pickup_location": "Pickup location",
    "issue": "What happened",
    "is_safe": "Caller safe",
    "destination": "Tow to",
    "language": "Call language",
}
LANGUAGE_WORDS = {"en": "English", "es": "Spanish", "fr": "French", "ht": "Haitian Creole"}


def log_only_line(kind, shop_id, public_id):
    if kind not in KINDS:
        raise ValueError(f"unknown alert kind {kind}")
    return f"{LOG_ONLY_HEADER}: new {kind} for shop {shop_id}, ticket {public_id}"


def email_subject(kind: str, shop_name: str, details: dict) -> str:
    title = KIND_TITLES[kind]
    flags = []
    if details.get("urgency") == "high":
        flags.append("HIGH")
    if kind == "tow" and details.get("is_safe") is False:
        flags.append("CALLER MAY BE UNSAFE")
    language = details.get("callback_language")
    if language and language != "en":
        flags.append(f"call back in {LANGUAGE_WORDS.get(language, language)}")
    flag_text = f" ({', '.join(flags)})" if flags else ""
    return f"[{shop_name}] {title}{flag_text}: {details.get('name', 'caller')}"


def email_text(kind: str, shop_name: str, public_id: str, details: dict, admin_url: str | None) -> str:
    lines = [f"{KIND_TITLES[kind]} for {shop_name}, ticket {public_id}.", ""]
    for key, label in DETAIL_LABELS.items():
        value = details.get(key)
        if value is None or value == "":
            continue
        if key in {"callback_language", "language"}:
            value = LANGUAGE_WORDS.get(value, value)
        if isinstance(value, bool):
            value = "yes" if value else "NO, they may be in danger"
        lines.append(f"{label}: {value}")
    if admin_url:
        lines += ["", f"Live board: {admin_url}"]
    lines += ["", "Sent by Bear, the shop's AI receptionist."]
    return "\n".join(lines)


def resend_sender(api_key: str, from_address: str) -> Callable[[list[str], str, str], None]:
    """Return a function that sends one plain-text email through Resend, raising on any failure."""

    def send(recipients: list[str], subject: str, text: str) -> None:
        body = json.dumps({"from": from_address, "to": recipients, "subject": subject, "text": text}).encode()
        request = urllib.request.Request(
            RESEND_URL,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=EMAIL_TIMEOUT_SECONDS) as response:
                response.read()
        except urllib.error.HTTPError as error:
            # Status only: the response body could echo recipients or content.
            raise RuntimeError(f"email API returned HTTP {error.code}") from None

    return send


@dataclass
class AlertTarget:
    shop_id: str
    shop_name: str
    recipients: list[str]
    admin_url: str | None = None


class StaffAlerter:
    def __init__(
        self,
        mode: str,
        *,
        email_sender: Callable[[list[str], str, str], None] | None = None,
        record_outcome: Callable[[str, str, str], None] | None = None,
        max_workers: int = 2,
    ):
        if mode not in MODES:
            raise ValueError(f"unknown ALERTS_MODE {mode}")
        if mode == "email" and email_sender is None:
            raise ValueError("ALERTS_MODE=email needs an email sender")
        self.mode = mode
        self.email_sender = email_sender
        self.record_outcome = record_outcome
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bear-alert")
        self._pending: set[Future] = set()
        self._lock = threading.Lock()

    def send(self, kind: str, target: AlertTarget, public_id: str, details: dict) -> None:
        """Notify staff about a saved booking, message or tow. Never raises."""
        try:
            if self.mode == "off":
                return
            if self.mode == "log":
                alerts_logger.info(log_only_line(kind, target.shop_id, public_id))
                return
            log_only_line(kind, target.shop_id, public_id)  # validates the kind
            if not target.recipients:
                alerts_logger.warning(
                    "STAFF ALERT not delivered: shop %s has no alerts.email recipients (%s %s)",
                    target.shop_id,
                    kind,
                    public_id,
                )
                return
            subject = email_subject(kind, target.shop_name, details)
            text = email_text(kind, target.shop_name, public_id, details, target.admin_url)
            future = self._executor.submit(self._deliver, kind, target, public_id, subject, text)
            with self._lock:
                self._pending.add(future)
            future.add_done_callback(self._forget)
            alerts_logger.info(f"{EMAIL_HEADER}: new {kind} for shop {target.shop_id}, ticket {public_id}")
        except Exception:
            alerts_logger.exception("staff alert failed for %s %s", kind, public_id)

    def _deliver(self, kind, target, public_id, subject, text):
        try:
            self.email_sender(target.recipients, subject, text)
            outcome = "sent"
            alerts_logger.info(f"STAFF ALERT emailed: {kind} for shop {target.shop_id}, ticket {public_id}")
        except Exception as error:
            outcome = "failed"
            # The error type and status only; never the message body or the recipients.
            alerts_logger.error(
                "STAFF ALERT email failed for %s %s: %s",
                kind,
                public_id,
                error if isinstance(error, RuntimeError) else type(error).__name__,
            )
        if self.record_outcome is not None:
            try:
                self.record_outcome(target.shop_id, public_id, outcome)
            except Exception:
                alerts_logger.exception("could not record alert outcome for %s", public_id)

    def _forget(self, future):
        with self._lock:
            self._pending.discard(future)

    def wait_idle(self, timeout: float = 10) -> None:
        """Block until queued emails finish (used by tests and on shutdown)."""
        with self._lock:
            pending = list(self._pending)
        for future in pending:
            future.result(timeout=timeout)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)


def warn_if_prod_log_only(mode, env):
    if env == "prod" and mode == "log":
        logging.getLogger("bear").warning(PROD_LOG_WARNING)
