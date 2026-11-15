"""
Shop Receptionist tool backend for an ElevenLabs agent.

ElevenLabs "webhook" tools POST JSON bodies to these endpoints during a call,
and the agent reads the JSON reply back to the caller.

Run:  uvicorn --factory main:create_app --reload --port 8000
Expose: ngrok http 8000   (then paste https://<id>.ngrok-free.app/tools/... into each tool)
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextvars
import hmac
import html
import json
import logging
import os
import re
import sys
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, BeforeValidator, Field

from bear.db import check_schema, make_engine
from bear.seed import DEMO_REPAIR_ORDERS
from bear.store import SlotFull, Store


@dataclass(frozen=True)
class Settings:
    tool_secret: str
    env: str
    admin_password: str
    database_url: str
    shop_id: str
    tool_deadline_ms: int = 4000
    admin_user: str = "admin"
    shop_name: str = "Vice City Auto"
    shop_tz: ZoneInfo = ZoneInfo("America/New_York")

    @classmethod
    def from_env(cls) -> Settings:
        try:
            deadline_ms = int(os.getenv("TOOL_DEADLINE_MS", "4000"))
            if deadline_ms <= 0:
                raise ValueError
        except ValueError:
            raise RuntimeError("TOOL_DEADLINE_MS must be a positive integer") from None
        return cls(
            tool_deadline_ms=deadline_ms,
            tool_secret=os.getenv("TOOL_SECRET", ""),
            env=os.getenv("BEAR_ENV", ""),
            admin_user=os.getenv("ADMIN_USER", "admin"),
            admin_password=os.getenv("ADMIN_PASSWORD", ""),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./vicecity.db"),
            shop_id=os.getenv("SHOP_ID", "vice-city-auto"),
            shop_name=os.getenv("SHOP_NAME", "Vice City Auto"),
            shop_tz=ZoneInfo(os.getenv("SHOP_TZ", "America/New_York")),
        )


OPEN_HOUR, CLOSE_HOUR = 8, 18  # Mon-Fri 8a-6p
SAT_OPEN, SAT_CLOSE = 9, 14  # Sat 9a-2p, closed Sunday
BAYS_PER_SLOT = 2

STATUS_TEXT = {
    "waiting_on_parts": "waiting on parts",
    "in_progress": "being worked on right now",
    "ready_for_pickup": "ready for pickup",
    "awaiting_approval": "waiting for the customer's approval on the estimate",
    "diagnosing": "being diagnosed",
}


REPAIR_STATUS_NEEDS = {
    "ok": True,
    "found": False,
    "needs": ["last_name", "phone_or_ro_number"],
    "message": "I need the last name on the repair order plus the phone number on the ticket or the RO number.",
}
REPAIR_STATUS_FAILURE = {
    "ok": True,
    "found": False,
    "message": "I couldn't verify that repair order. Offer to take a message for the service writer.",
}


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold().strip()


# ---------- validation and transport ----------
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


Name = Annotated[str, Field(min_length=1, max_length=80), BeforeValidator(clean_text)]
Text = Annotated[str, Field(min_length=1, max_length=500), BeforeValidator(clean_text)]
OptionalName = Annotated[str, Field(max_length=80), BeforeValidator(clean_text)]
OptionalText = Annotated[str, Field(max_length=500), BeforeValidator(clean_text)]
Phone = Annotated[str, BeforeValidator(normalize_phone)]
Language = Annotated[Literal["en", "es", "fr", "ht"], BeforeValidator(lowercase)]
Urgency = Annotated[Literal["low", "normal", "high"], BeforeValidator(lowercase)]


class BookAppointment(BaseModel):
    customer_name: Name
    phone: Phone
    vehicle: Name = Field(description="year make model")
    service: Text = Field(description="what the customer needs")
    preferred_date: Annotated[str, BeforeValidator(validate_date)]
    preferred_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    language: Language = "en"
    notes: OptionalText | None = None


class RepairStatus(BaseModel):
    phone: Annotated[str | None, BeforeValidator(integer_to_string)] = Field(default=None, max_length=32)
    ro_number: Annotated[str | None, BeforeValidator(integer_to_string)] = Field(default=None, max_length=20)
    last_name: Annotated[str | None, BeforeValidator(clean_text)] = Field(default=None, max_length=80)


class TowRequest(BaseModel):
    customer_name: Name
    phone: Phone
    vehicle: Name
    pickup_location: Text = Field(description="street address, cross streets, or landmark")
    issue: Text = Field(description="what happened")
    is_safe: bool = Field(description="caller confirmed they are in a safe place")
    destination: OptionalText | None = None
    language: Language = "en"


class TakeMessage(BaseModel):
    caller_name: Name
    phone: Phone
    reason: Text
    urgency: Urgency = "normal"
    callback_language: Language = "en"
    vehicle: OptionalName | None = None


def invalid_input(field, message):
    return {"ok": False, "error": "invalid_input", "field": field, "message": message}


def validation_message(field, error):
    hints = {
        "phone": "I need a 10-digit US number, area code and all, or an international number starting with plus and the country code.",
        "preferred_date": "I need the date as year-month-day, like 2026-11-20.",
        "preferred_time": "I need the time in 24-hour format, like 09:30 or 14:00.",
        "language": "Language must be en, es, fr or ht.",
        "callback_language": "Language must be en, es, fr or ht.",
        "urgency": "Urgency must be low, normal or high.",
        "is_safe": "I need to know if the caller is safe: true or false.",
    }
    label = field.replace("_", " ").capitalize()
    if error["type"] == "string_too_long":
        limit = error["ctx"]["max_length"]
        return f"{label} is too long; keep it under {limit} characters."
    detail = f"{error.get('msg', '')} {(error.get('ctx') or {}).get('error', '')}".lower()
    if field == "phone" and "extension" in detail:
        return "I need the phone number without the extension."
    return hints.get(field, f"{label} is required.")


# Only derived, allowlisted fields belong in request logs; never log exceptions or bodies.
# Mutable bound so a worker thread sees abandoned=True after the event loop times out.
_REQUEST_BOUND = contextvars.ContextVar("tool_bound", default=None)
_TOOL_NAMES = {"book_appointment", "check_repair_status", "request_tow", "take_message"}
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class _DeadlineExceeded(Exception):
    """Private signal preventing an abandoned worker from persisting changes."""


def write_is_open() -> bool:
    bound = _REQUEST_BOUND.get()
    if not bound:
        return True
    if bound.get("abandoned"):
        return False
    deadline = bound.get("deadline")
    return deadline is None or time.monotonic() < deadline


def persist_json(path: Path, db: dict) -> None:
    """Write the data file only if this request is still allowed to commit."""
    if not write_is_open():
        raise _DeadlineExceeded
    path.write_text(json.dumps(db, indent=2, ensure_ascii=False))


def configure_logging():
    """Write plain JSON to stdout without interfering with log capture."""
    logger = logging.getLogger("bear")
    handlers = [h for h in logger.handlers if h.name == "bear-json"]
    for handler in handlers:
        logger.removeHandler(handler)
        handler.close()
    handler = logging.StreamHandler(sys.stdout)
    handler.name = "bear-json"
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = True


class RequestLoggingMiddleware:
    """Bound tool work and log only metadata, validated IDs and masked phones."""

    def __init__(self, app, deadline_ms):
        self.app = app
        self.deadline_seconds = deadline_ms / 1000

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.monotonic()
        path, method = scope["path"], scope["method"]
        is_tool = method == "POST" and path.startswith("/tools/")
        name = path[len("/tools/") :] if is_tool else None
        headers = dict(scope["headers"])

        def correlation(key):
            value = headers.get(key, b"").decode("latin-1")
            return value if _CORRELATION_ID.fullmatch(value) else None

        request_id = correlation(b"x-request-id") or uuid.uuid4().hex
        entry = {
            "request_id": request_id,
            "method": method,
            "path": path,
            "tool": name if name in _TOOL_NAMES else None,
        }
        conversation_id = correlation(b"x-conversation-id")
        if conversation_id:
            entry["conversation_id"] = conversation_id
        status, body, abandoned = 500, bytearray(), False

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"] + [
                    (b"x-request-id", request_id.encode("ascii"))
                ]
            elif message["type"] == "http.response.body" and is_tool:
                body.extend(message.get("body", b""))
            await send(message)

        async def downstream_send(message):
            if not abandoned:
                await capture(message)

        async def run_tool():
            try:
                await self.app(scope, receive, downstream_send)
            except Exception as exc:
                if not abandoned:
                    entry["exc_type"] = type(exc).__name__
                    await JSONResponse(
                        {
                            "ok": False,
                            "error": "internal_error",
                            "message": "Sorry, something went wrong on our end. Let me take a message.",
                        }
                    )(scope, receive, downstream_send)

        bound = {"deadline": start + self.deadline_seconds, "abandoned": False} if is_tool else None
        token = _REQUEST_BOUND.set(bound)
        try:
            if is_tool:
                task = asyncio.create_task(run_tool())
                # Shielding lets us return promptly even while a sync worker is running.
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task), max(0, start + self.deadline_seconds - time.monotonic())
                    )
                except TimeoutError:
                    abandoned = True
                    bound["abandoned"] = True
                    task.cancel()
                    task.add_done_callback(self._consume_task)
                    body.clear()
                    await JSONResponse(
                        {
                            "ok": False,
                            "error": "timeout",
                            "message": "Sorry, our system is slow right now. Let me take a message.",
                        }
                    )(scope, receive, capture)
            else:
                await self.app(scope, receive, capture)
        finally:
            _REQUEST_BOUND.reset(token)
            result = {}
            if is_tool and status == 200:
                try:
                    result = json.loads(body)
                except ValueError:
                    # Invalid JSON leaves logging metadata at its default values.
                    result = {}
            entry.update(
                ts=datetime.now(UTC).isoformat(),
                level="info",
                event="request",
                status=status,
                latency_ms=round((time.monotonic() - start) * 1000, 1),
                ok=result.get("ok", 200 <= status < 300),
            )
            error = {
                401: "unauthorized",
                400: "transport",
                413: "transport",
                415: "transport",
                404: "not_found",
                503: "unavailable",
            }.get(status)
            if result.get("error") in {"invalid_input", "timeout", "internal_error"}:
                error = result["error"]
                if error == "invalid_input":
                    entry["field"] = result["field"]
            if error:
                entry["error"] = error
            phone = scope.get("state", {}).get("bear_body", {}).get("phone")
            if isinstance(phone, (str, int)) and not isinstance(phone, bool):
                digits = re.sub(r"\D", "", str(phone))
                if len(digits) >= 4:
                    entry["phone"] = "***" + digits[-4:]
            logging.getLogger("bear.request").info(json.dumps(entry, separators=(",", ":"), ensure_ascii=True))

    @staticmethod
    def _consume_task(task):
        """Retrieve abandoned errors without logging their potentially private text."""
        if not task.cancelled():
            task.exception()


class ToolTransportMiddleware:
    def __init__(self, app, tool_secret=""):
        self.app = app
        self.tool_secret = tool_secret

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith("/tools/"):
            return await self.app(scope, receive, send)
        # Auth runs first so a bad secret never has its body parsed or a phone logged.
        headers = dict(scope["headers"])

        async def reject(status, detail):
            await JSONResponse(status_code=status, content={"detail": detail})(scope, receive, send)

        provided = headers.get(b"x-tool-secret", b"").decode("latin-1")
        if self.tool_secret and not hmac.compare_digest(provided.encode("utf-8"), self.tool_secret.encode("utf-8")):
            return await reject(401, "bad tool secret")

        content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if content_type != b"application/json":
            await reject(415, "Content-Type must be application/json")
            return
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > 16384:
            await reject(413, "request body too large")
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 16384:
                await reject(413, "request body too large")
                return
            if not message.get("more_body", False):
                break
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            await reject(400, "invalid JSON body")
            return
        if not isinstance(parsed, dict):
            await reject(400, "invalid JSON body")
            return

        scope.setdefault("state", {})["bear_body"] = parsed

        async def replay():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def create_app() -> FastAPI:
    configure_logging()
    settings = Settings.from_env()
    if settings.env != "dev" and len(settings.tool_secret) < 32:
        raise RuntimeError("TOOL_SECRET must be set to at least 32 characters (or set BEAR_ENV=dev for local testing)")
    if not settings.tool_secret:
        logging.getLogger("bear").warning("TOOL_SECRET is unset; tool authentication is disabled in dev")

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", settings.shop_id):
        raise RuntimeError("invalid SHOP_ID")
    engine = None
    try:
        engine = make_engine(settings.database_url, timeout_seconds=settings.tool_deadline_ms / 1000)
        check_schema(engine)
    except Exception:
        if engine is not None:
            engine.dispose()
        raise RuntimeError("Database unavailable or schema missing; run alembic upgrade head") from None

    def check_deadline():
        if not write_is_open():
            raise _DeadlineExceeded

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(lifespan=lifespan, title=f"{settings.shop_name} AI Receptionist tools", version="0.1.0")
    app.state.settings = settings
    store = app.state.store = Store(engine, write_guard=check_deadline)
    app.add_middleware(ToolTransportMiddleware, tool_secret=settings.tool_secret)
    app.add_middleware(RequestLoggingMiddleware, deadline_ms=settings.tool_deadline_ms)

    @app.exception_handler(RequestValidationError)
    async def tool_validation_error(request, exc):
        if request.url.path.startswith("/tools/"):
            for error in exc.errors():
                if error["loc"][0] == "body" and len(error["loc"]) > 1:
                    field = error["loc"][1]
                    return JSONResponse(invalid_input(field, validation_message(field, error)))
        return await request_validation_exception_handler(request, exc)

    def check_secret(x_tool_secret: str | None = Header(default=None)):
        if settings.tool_secret and not hmac.compare_digest(
            (x_tool_secret or "").encode("utf-8"), settings.tool_secret.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="bad tool secret")

    def check_admin(authorization: str | None = Header(default=None)):
        if not settings.admin_password:
            raise HTTPException(status_code=503, detail="admin disabled")
        unauthorized = HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": 'Basic realm="bear-admin"'},
        )
        try:
            scheme, encoded = (authorization or "").split(" ", 1)
            if scheme.lower() != "basic":
                raise ValueError("Expected Basic authorization")
            credentials = base64.b64decode(encoded, validate=True)
            username, password = credentials.split(b":", 1)
        except (ValueError, binascii.Error):
            raise unauthorized from None
        user_ok = hmac.compare_digest(username, settings.admin_user.encode("utf-8"))
        password_ok = hmac.compare_digest(password, settings.admin_password.encode("utf-8"))
        if not (user_ok & password_ok):
            raise unauthorized

    def digits(s: str | None) -> str:
        return re.sub(r"\D", "", s or "")[-10:]

    # ---------- helpers ----------
    def slot_ok(dt: datetime) -> str | None:
        wd = dt.weekday()
        if wd == 6:
            return "We're closed Sundays."
        o, c = (SAT_OPEN, SAT_CLOSE) if wd == 5 else (OPEN_HOUR, CLOSE_HOUR)
        if not (o <= dt.hour < c):
            return f"That day we take drop-offs between {o}:00 and {c}:00."
        if dt < datetime.now(settings.shop_tz):
            return "That time already passed."
        return None

    def next_slots(start: datetime, n: int = 3) -> list[str]:
        out, t = [], start.replace(minute=0, second=0, microsecond=0)
        counts = store.booked_counts(settings.shop_id, t, t + timedelta(hours=24 * 14 + 1))
        for _ in range(24 * 14):
            t += timedelta(hours=1)
            if slot_ok(t) is None and counts.get(t.astimezone(UTC), 0) < BAYS_PER_SLOT:
                out.append(t.strftime("%A %b %d at %I:%M %p").replace(" 0", " "))
                if len(out) == n:
                    break
        return out

    # ---------- endpoints ----------
    @app.get("/health")
    def health():
        return {"ok": True, "time": datetime.now(settings.shop_tz).isoformat()}

    @app.post("/tools/book_appointment", dependencies=[Depends(check_secret)])
    def book_appointment(req: BookAppointment):
        if date.fromisoformat(req.preferred_date) > datetime.now(settings.shop_tz).date() + timedelta(days=60):
            return invalid_input("preferred_date", "I can only book up to 60 days out.")
        dt = datetime.strptime(f"{req.preferred_date} {req.preferred_time}", "%Y-%m-%d %H:%M").replace(
            tzinfo=settings.shop_tz
        )
        if dt.minute or dt.second or dt.microsecond:
            return invalid_input("preferred_time", "I need a time on the hour, like 09:00 or 14:00.")
        problem = slot_ok(dt)
        if problem is None:
            fields = req.model_dump(exclude={"preferred_date", "preferred_time"})
            try:
                appt = store.book_appointment(
                    settings.shop_id, dt.replace(minute=0, second=0, microsecond=0), BAYS_PER_SLOT, **fields
                )
            except SlotFull:
                problem = "That slot is full."
        if problem:
            return {
                "ok": True,
                "booked": False,
                "message": problem,
                "alternatives": next_slots(max(dt, datetime.now(settings.shop_tz))),
            }
        return {
            "ok": True,
            "booked": True,
            "confirmation": appt["public_id"],
            "when": dt.strftime("%A %b %d at %I:%M %p").replace(" 0", " "),
            "message": f"Booked {req.vehicle} for {req.service}. Bring the keys to the front desk.",
        }

    @app.post("/tools/check_repair_status", dependencies=[Depends(check_secret)])
    def check_repair_status(req: RepairStatus):
        last_name = (req.last_name or "").strip()
        phone = (req.phone or "").strip()
        ro = (req.ro_number or "").strip()
        if not last_name or not (phone or ro):
            store.record_audit(settings.shop_id, "repair_status.lookup", outcome="needs_info")
            return REPAIR_STATUS_NEEDS

        names = {normalize_name(last_name), normalize_name(last_name.split()[-1])}
        phone_digits = re.sub(r"\D", "", phone)
        hits = []
        for record in store.list_repair_orders(settings.shop_id):
            customer_tokens = record["customer_name"].split()
            if not customer_tokens or normalize_name(customer_tokens[-1]) not in names:
                continue
            if phone and (len(phone_digits) < 10 or digits(record["phone"]) != phone_digits[-10:]):
                continue
            if ro and record["ro_number"] != ro:
                continue
            hits.append(record)
        if len(hits) != 1:
            store.record_audit(settings.shop_id, "repair_status.lookup", outcome="failed")
            return REPAIR_STATUS_FAILURE
        r = hits[0]
        store.record_audit(settings.shop_id, "repair_status.lookup", r["public_id"], outcome="verified")
        return {
            "ok": True,
            "found": True,
            "ro_number": r["ro_number"],
            "customer_first_name": r["customer_name"].split()[0],
            "vehicle": r["vehicle"],
            "status": STATUS_TEXT.get(r["status"], r["status"]),
            "details": r["note"],
            "eta": r["eta"],
            "estimated_balance": r["balance_estimate"],
        }

    @app.post("/tools/request_tow", dependencies=[Depends(check_secret)])
    def request_tow(req: TowRequest):
        fields = req.model_dump()
        fields["destination"] = req.destination or settings.shop_name
        tow = store.create_tow(settings.shop_id, **fields, status="dispatch_requested")
        msg = "Tow request logged; dispatcher will call back within 15 minutes with an ETA."
        if not req.is_safe:
            msg = "Caller may be unsafe: tell them to call 911 now if anyone is hurt or they're in traffic. " + msg
        return {"ok": True, "logged": True, "ticket": tow["public_id"], "message": msg}

    @app.post("/tools/take_message", dependencies=[Depends(check_secret)])
    def take_message(req: TakeMessage):
        m = store.create_message(settings.shop_id, **req.model_dump())
        lang = {
            "ht": " A Creole-speaking team member will call back.",
            "es": " Alguien que habla español le llamará.",
        }.get(req.callback_language, "")
        return {
            "ok": True,
            "saved": True,
            "ticket": m["public_id"],
            "message": "Message saved for the service writer." + lang,
        }

    @app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(check_admin)])
    def admin(shop: str | None = None):
        """Live board for the requested shop."""
        shop = settings.shop_id if shop is None else shop
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", shop):
            raise HTTPException(status_code=400, detail="invalid shop")
        db = store.board(shop, 50)

        def display(value):
            if value is None:
                return ""
            if isinstance(value, datetime):
                return value.astimezone(settings.shop_tz).strftime("%Y-%m-%d %H:%M %Z")
            return str(value)

        def table(rows, cols):
            h = "".join(f"<th>{html.escape(str(c), quote=True)}</th>" for c in cols)
            b = "".join(
                "<tr>" + "".join(f"<td>{html.escape(display(r.get(c, '')), quote=True)}</td>" for c in cols) + "</tr>"
                for r in rows
            )
            return f"<table><tr>{h}</tr>{b}</table>"

        return f"""<html><head><meta http-equiv="refresh" content="3"><title>{html.escape(str(settings.shop_name), quote=True)} board</title>
    <style>body{{font-family:system-ui;margin:24px}}table{{border-collapse:collapse;margin-bottom:24px}}
    td,th{{border:1px solid #ccc;padding:6px 10px}}th{{background:#222;color:#fff}}</style></head><body>
    <h1>{html.escape(str(settings.shop_name), quote=True)}: AI receptionist live board</h1>
    <h2>Appointments</h2>{table(db["appointments"], ["public_id", "slot_start", "customer_name", "phone", "vehicle", "service", "language"])}
    <h2>Tow requests</h2>{table(db["tows"], ["public_id", "created_at", "customer_name", "phone", "pickup_location", "issue", "is_safe"])}
    <h2>Messages</h2>{table(db["messages"], ["public_id", "created_at", "caller_name", "phone", "vehicle", "reason", "urgency", "callback_language"])}
    <h2>Open repair orders</h2>{table(db["repair_orders"], ["ro_number", "customer_name", "vehicle", "status", "eta"])}
    </body></html>"""

    @app.post("/admin/reset")
    def reset(authorization: str | None = Header(default=None)):
        if settings.env != "dev":
            raise HTTPException(status_code=404, detail="Not Found")
        check_admin(authorization)
        store.reset_shop(settings.shop_id)
        for record in DEMO_REPAIR_ORDERS:
            store.upsert_repair_order(settings.shop_id, **record)
        return {"reset": True}

    return app
