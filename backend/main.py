"""
Shop Receptionist tool backend for an ElevenLabs agent.

ElevenLabs "webhook" tools POST JSON bodies to these endpoints during a call,
and the agent reads the JSON reply back to the caller.

Run:  uvicorn --factory main:create_app --reload --port 8000
Expose: ngrok http 8000   (then paste https://<id>.ngrok-free.app/tools/... into each tool)
"""
from __future__ import annotations

import asyncio
import contextvars
import sys
import time
import base64
import binascii
import hmac
import html
import json
import logging
import os
import re
import uuid
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, BeforeValidator, Field


@dataclass(frozen=True)
class Settings:
    tool_secret: str
    env: str
    admin_password: str
    data_file: Path
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
            data_file=Path(os.getenv("DATA_FILE", Path(__file__).with_name("data.json"))),
            shop_name=os.getenv("SHOP_NAME", "Vice City Auto"),
            shop_tz=ZoneInfo(os.getenv("SHOP_TZ", "America/New_York")),
        )


OPEN_HOUR, CLOSE_HOUR = 8, 18          # Mon-Fri 8a-6p
SAT_OPEN, SAT_CLOSE = 9, 14            # Sat 9a-2p, closed Sunday
BAYS_PER_SLOT = 2

SEED = {
    "repairs": [
        {"ro": "1001", "customer": "Carlos Rivera", "phone": "+13055550101", "plate": "MIA123",
         "vehicle": "2017 Chevy Malibu", "status": "waiting_on_parts",
         "note": "Front-right tire and wheel bearing. Bearing arrives tomorrow morning.",
         "eta": "Tomorrow by 3 PM", "balance_estimate": "$480"},
        {"ro": "1002", "customer": "Marie Jean-Baptiste", "phone": "+17865550102", "plate": "KRY456",
         "vehicle": "2015 Toyota Camry", "status": "in_progress",
         "note": "Rear bumper repair and paint. In the paint booth now.",
         "eta": "Thursday end of day", "balance_estimate": "$1,250"},
        {"ro": "1003", "customer": "Mike O'Brien", "phone": "+19545550103", "plate": "NYC789",
         "vehicle": "2012 Ford F-150", "status": "ready_for_pickup",
         "note": "Brakes done, pads and rotors front. Ready to go.",
         "eta": "Ready now", "balance_estimate": "$615"},
        {"ro": "1004", "customer": "Ana Gómez", "phone": "+13055550104", "plate": "FLA321",
         "vehicle": "2019 Honda Civic", "status": "awaiting_approval",
         "note": "Estimate sent for A/C compressor. Need customer approval to proceed.",
         "eta": "1 day after approval", "balance_estimate": "$1,100"},
    ],
    "appointments": [],
    "tows": [],
    "messages": [],
}

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
def clean_text(value):
    if isinstance(value, str):
        chars = []
        for char in value:
            category = unicodedata.category(char)
            if category == "Cf":
                continue  # zero-width, bidi controls, BOM
            chars.append(" " if category == "Cc" else char)
        return " ".join("".join(chars).split())
    return value


def integer_to_string(value):
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


def is_nanp(digits):
    # Area code and exchange must be [2-9]XX; this rejects 000-000-0000 and similar fakes.
    return bool(re.fullmatch(r"[2-9]\d{2}[2-9]\d{6}", digits))


def normalize_phone(value):
    value = integer_to_string(value)
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError("invalid phone")
    plus = value.strip().startswith("+")
    digits = re.sub(r"\D", "", value)
    if plus and 8 <= len(digits) <= 15:
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
    notes: Optional[OptionalText] = None


class RepairStatus(BaseModel):
    phone: Annotated[Optional[str], BeforeValidator(integer_to_string)] = Field(default=None, max_length=32)
    ro_number: Annotated[Optional[str], BeforeValidator(integer_to_string)] = Field(default=None, max_length=20)
    last_name: Optional[str] = Field(default=None, max_length=80)


class TowRequest(BaseModel):
    customer_name: Name
    phone: Phone
    vehicle: Name
    pickup_location: Text = Field(description="street address, cross streets, or landmark")
    issue: Text = Field(description="what happened")
    is_safe: bool = Field(description="caller confirmed they are in a safe place")
    destination: Optional[OptionalText] = None
    language: Language = "en"


class TakeMessage(BaseModel):
    caller_name: Name
    phone: Phone
    reason: Text
    urgency: Urgency = "normal"
    callback_language: Language = "en"
    vehicle: Optional[OptionalName] = None


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
    return hints.get(field, f"{label} is required.")


# Only derived, allowlisted fields belong in request logs; never log exceptions or bodies.
_REQUEST_DEADLINE = contextvars.ContextVar("tool_deadline", default=None)
_TOOL_NAMES = {"book_appointment", "check_repair_status", "request_tow", "take_message"}
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class _DeadlineExceeded(Exception):
    """Private signal preventing an abandoned worker from persisting changes."""


def read_db(path: Path) -> Optional[dict]:
    """Read the data seam on every load, including the initial seed."""
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


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
            return await self.app(scope, receive, send)
        start = time.monotonic()
        path, method = scope["path"], scope["method"]
        is_tool = method == "POST" and path.startswith("/tools/")
        name = path[len("/tools/"):] if is_tool else None
        headers = dict(scope["headers"])

        def correlation(key):
            value = headers.get(key, b"").decode("latin-1")
            return value if _CORRELATION_ID.fullmatch(value) else None

        request_id = correlation(b"x-request-id") or uuid.uuid4().hex
        entry = {"request_id": request_id, "method": method, "path": path,
                 "tool": name if name in _TOOL_NAMES else None}
        conversation_id = correlation(b"x-conversation-id")
        if conversation_id:
            entry["conversation_id"] = conversation_id
        status, body, abandoned = 500, bytearray(), False

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", [])
                                      if k.lower() != b"x-request-id"] + [(b"x-request-id", request_id.encode("ascii"))]
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
                    await JSONResponse({"ok": False, "error": "internal_error",
                        "message": "Sorry, something went wrong on our end. Let me take a message."})(scope, receive, downstream_send)

        token = _REQUEST_DEADLINE.set(start + self.deadline_seconds if is_tool else None)
        try:
            if is_tool:
                task = asyncio.create_task(run_tool())
                # Shielding lets us return promptly even while a sync worker is running.
                try:
                    await asyncio.wait_for(asyncio.shield(task), max(0, start + self.deadline_seconds - time.monotonic()))
                except asyncio.TimeoutError:
                    abandoned = True
                    task.cancel()
                    task.add_done_callback(self._consume_task)
                    body.clear()
                    await JSONResponse({"ok": False, "error": "timeout",
                        "message": "Sorry, our system is slow right now. Let me take a message."})(scope, receive, capture)
            else:
                await self.app(scope, receive, capture)
        finally:
            _REQUEST_DEADLINE.reset(token)
            result = {}
            if is_tool and status == 200:
                try:
                    result = json.loads(body)
                except ValueError:
                    pass
            entry.update(ts=datetime.now(timezone.utc).isoformat(), level="info", event="request",
                         status=status, latency_ms=round((time.monotonic() - start) * 1000, 1),
                         ok=result.get("ok", 200 <= status < 300))
            error = {401: "unauthorized", 400: "transport", 413: "transport", 415: "transport",
                     404: "not_found", 503: "unavailable"}.get(status)
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
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith("/tools/"):
            return await self.app(scope, receive, send)
        # Transport checks run before FastAPI parses the body or checks the tool secret.
        headers = dict(scope["headers"])

        async def reject(status, detail):
            await JSONResponse(status_code=status, content={"detail": detail})(scope, receive, send)

        content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if content_type != b"application/json":
            return await reject(415, "Content-Type must be application/json")
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > 16384:
            return await reject(413, "request body too large")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 16384:
                return await reject(413, "request body too large")
            if not message.get("more_body", False):
                break
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return await reject(400, "invalid JSON body")
        if not isinstance(parsed, dict):
            return await reject(400, "invalid JSON body")

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

    app = FastAPI(title=f"{settings.shop_name} AI Receptionist tools", version="0.1.0")
    app.state.settings = settings
    app.add_middleware(ToolTransportMiddleware)
    app.add_middleware(RequestLoggingMiddleware, deadline_ms=settings.tool_deadline_ms)

    @app.exception_handler(RequestValidationError)
    async def tool_validation_error(request, exc):
        if request.url.path.startswith("/tools/"):
            for error in exc.errors():
                if error["loc"][0] == "body" and len(error["loc"]) > 1:
                    field = error["loc"][1]
                    return JSONResponse(invalid_input(field, validation_message(field, error)))
        return await request_validation_exception_handler(request, exc)


    def check_secret(x_tool_secret: Optional[str] = Header(default=None)):
        if settings.tool_secret and not hmac.compare_digest(
            (x_tool_secret or "").encode("utf-8"), settings.tool_secret.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="bad tool secret")

    def check_admin(authorization: Optional[str] = Header(default=None)):
        if not settings.admin_password:
            raise HTTPException(status_code=503, detail="admin disabled")
        unauthorized = HTTPException(
            status_code=401, detail="Not authenticated",
            headers={"WWW-Authenticate": 'Basic realm="bear-admin"'},
        )
        try:
            scheme, encoded = (authorization or "").split(" ", 1)
            if scheme.lower() != "basic":
                raise ValueError("Expected Basic authorization")
            credentials = base64.b64decode(encoded, validate=True)
            username, password = credentials.split(b":", 1)
        except (ValueError, binascii.Error):
            raise unauthorized
        user_ok = hmac.compare_digest(username, settings.admin_user.encode("utf-8"))
        password_ok = hmac.compare_digest(password, settings.admin_password.encode("utf-8"))
        if not (user_ok & password_ok):
            raise unauthorized

    def load() -> dict:
        db = read_db(settings.data_file)
        if db is not None:
            return db
        save(SEED)
        return json.loads(json.dumps(SEED))

    def save(db: dict) -> None:
        deadline = _REQUEST_DEADLINE.get()
        if deadline is not None and time.monotonic() >= deadline:
            raise _DeadlineExceeded
        settings.data_file.write_text(json.dumps(db, indent=2, ensure_ascii=False))

    def digits(s: Optional[str]) -> str:
        return re.sub(r"\D", "", s or "")[-10:]

    # ---------- helpers ----------
    def slot_ok(dt: datetime) -> Optional[str]:
        wd = dt.weekday()
        if wd == 6:
            return "We're closed Sundays."
        o, c = (SAT_OPEN, SAT_CLOSE) if wd == 5 else (OPEN_HOUR, CLOSE_HOUR)
        if not (o <= dt.hour < c):
            return f"That day we take drop-offs between {o}:00 and {c}:00."
        if dt < datetime.now(settings.shop_tz):
            return "That time already passed."
        return None

    def next_slots(db: dict, start: datetime, n: int = 3) -> list[str]:
        out, t = [], start.replace(minute=0, second=0, microsecond=0)
        for _ in range(24 * 14):
            t += timedelta(hours=1)
            if slot_ok(t) is None and count_at(db, t) < BAYS_PER_SLOT:
                out.append(t.strftime("%A %b %d at %I:%M %p").replace(" 0", " "))
                if len(out) == n:
                    break
        return out

    def count_at(db: dict, t: datetime) -> int:
        key = t.strftime("%Y-%m-%dT%H")
        return sum(1 for a in db["appointments"] if a["start"].startswith(key))

    # ---------- endpoints ----------
    @app.get("/health")
    def health():
        return {"ok": True, "time": datetime.now(settings.shop_tz).isoformat()}

    @app.post("/tools/book_appointment", dependencies=[Depends(check_secret)])
    def book_appointment(req: BookAppointment):
        if date.fromisoformat(req.preferred_date) > datetime.now(settings.shop_tz).date() + timedelta(days=60):
            return invalid_input("preferred_date", "I can only book up to 60 days out.")
        db = load()
        dt = datetime.strptime(f"{req.preferred_date} {req.preferred_time}", "%Y-%m-%d %H:%M").replace(tzinfo=settings.shop_tz)
        problem = slot_ok(dt) or ("That slot is full." if count_at(db, dt) >= BAYS_PER_SLOT else None)
        if problem:
            return {"ok": True, "booked": False, "message": problem, "alternatives": next_slots(db, max(dt, datetime.now(settings.shop_tz)))}
        appt = {"id": "A" + uuid.uuid4().hex[:6].upper(), "start": dt.isoformat(), **req.model_dump(),
                "created": datetime.now(settings.shop_tz).isoformat()}
        db["appointments"].append(appt)
        save(db)
        return {"ok": True, "booked": True, "confirmation": appt["id"],
                "when": dt.strftime("%A %b %d at %I:%M %p").replace(" 0", " "),
                "message": f"Booked {req.vehicle} for {req.service}. Bring the keys to the front desk."}

    @app.post("/tools/check_repair_status", dependencies=[Depends(check_secret)])
    def check_repair_status(req: RepairStatus):
        last_name = (req.last_name or "").strip()
        phone = (req.phone or "").strip()
        ro = (req.ro_number or "").strip()
        if not last_name or not (phone or ro):
            return REPAIR_STATUS_NEEDS

        names = {normalize_name(last_name), normalize_name(last_name.split()[-1])}
        phone_digits = re.sub(r"\D", "", phone)
        hits = []
        for record in load()["repairs"]:
            customer_tokens = record["customer"].split()
            if not customer_tokens or normalize_name(customer_tokens[-1]) not in names:
                continue
            if phone and (len(phone_digits) < 10 or digits(record["phone"]) != phone_digits[-10:]):
                continue
            if ro and record["ro"] != ro:
                continue
            hits.append(record)
        if len(hits) != 1:
            return REPAIR_STATUS_FAILURE
        r = hits[0]
        return {"ok": True, "found": True, "ro_number": r["ro"], "customer_first_name": r["customer"].split()[0],
                "vehicle": r["vehicle"], "status": STATUS_TEXT.get(r["status"], r["status"]),
                "details": r["note"], "eta": r["eta"], "estimated_balance": r["balance_estimate"]}

    @app.post("/tools/request_tow", dependencies=[Depends(check_secret)])
    def request_tow(req: TowRequest):
        db = load()
        tow = {"id": "T" + uuid.uuid4().hex[:6].upper(), **req.model_dump(),
               "destination": req.destination or settings.shop_name, "created": datetime.now(settings.shop_tz).isoformat(),
               "status": "dispatch_requested"}
        db["tows"].append(tow)
        save(db)
        msg = "Tow request logged; dispatcher will call back within 15 minutes with an ETA."
        if not req.is_safe:
            msg = "Caller may be unsafe: tell them to call 911 now if anyone is hurt or they're in traffic. " + msg
        return {"ok": True, "logged": True, "ticket": tow["id"], "message": msg}

    @app.post("/tools/take_message", dependencies=[Depends(check_secret)])
    def take_message(req: TakeMessage):
        db = load()
        m = {"id": "M" + uuid.uuid4().hex[:6].upper(), **req.model_dump(), "created": datetime.now(settings.shop_tz).isoformat()}
        db["messages"].append(m)
        save(db)
        lang = {"ht": " A Creole-speaking team member will call back.", "es": " Alguien que habla español le llamará."}.get(req.callback_language, "")
        return {"ok": True, "saved": True, "ticket": m["id"], "message": "Message saved for the service writer." + lang}

    @app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(check_admin)])
    def admin():
        """Live board for the demo: refresh while the call is happening."""
        db = load()
        def table(rows, cols):
            h = "".join(f"<th>{html.escape(str(c), quote=True)}</th>" for c in cols)
            b = "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(c,'')), quote=True)}</td>" for c in cols) + "</tr>" for r in reversed(rows))
            return f"<table><tr>{h}</tr>{b}</table>"
        return f"""<html><head><meta http-equiv="refresh" content="3"><title>{html.escape(str(settings.shop_name), quote=True)} board</title>
    <style>body{{font-family:system-ui;margin:24px}}table{{border-collapse:collapse;margin-bottom:24px}}
    td,th{{border:1px solid #ccc;padding:6px 10px}}th{{background:#222;color:#fff}}</style></head><body>
    <h1>{html.escape(str(settings.shop_name), quote=True)}: AI receptionist live board</h1>
    <h2>Appointments</h2>{table(db['appointments'], ['id','start','customer_name','phone','vehicle','service','language'])}
    <h2>Tow requests</h2>{table(db['tows'], ['id','created','customer_name','phone','pickup_location','issue','is_safe'])}
    <h2>Messages</h2>{table(db['messages'], ['id','created','caller_name','phone','reason','urgency','callback_language'])}
    <h2>Open repair orders</h2>{table(db['repairs'], ['ro','customer','vehicle','status','eta'])}
    </body></html>"""

    @app.post("/admin/reset")
    def reset(authorization: Optional[str] = Header(default=None)):
        if settings.env != "dev":
            raise HTTPException(status_code=404, detail="Not Found")
        check_admin(authorization)
        save(SEED)
        return {"reset": True}

    return app
