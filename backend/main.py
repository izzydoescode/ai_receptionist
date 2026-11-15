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

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from bear.db import check_schema, make_engine
from bear.initiation import InitRequest, shop_initiation, unknown_initiation
from bear.post_call import parse_transcription
from bear.seed import DEMO_REPAIR_ORDERS
from bear.shops import LANGUAGE_NAMES, SHOP_ID_PATTERN, TOW_FALLBACK_REPLY, UNKNOWN_SHOP_ID, ShopConfig, load_shops
from bear.store import SlotFull, Store
from bear.webhook_signature import SignatureError, verify_signature

POST_CALL_MAX_BYTES = 2 * 1024 * 1024  # transcription events only; leave "Send audio data" off
DEFAULT_SHOPS_DIR = Path(__file__).resolve().parent.parent / "shops"


@dataclass(frozen=True)
class Settings:
    tool_secret: str
    env: str
    admin_password: str
    database_url: str
    shop_id: str
    shops_dir: str = str(DEFAULT_SHOPS_DIR)
    init_webhook_secret: str = ""
    post_call_webhook_secret: str = ""
    tool_deadline_ms: int = 4000
    admin_user: str = "admin"

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
            init_webhook_secret=os.getenv("INIT_WEBHOOK_SECRET", ""),
            post_call_webhook_secret=os.getenv("POST_CALL_WEBHOOK_SECRET", ""),
            env=os.getenv("BEAR_ENV", ""),
            admin_user=os.getenv("ADMIN_USER", "admin"),
            admin_password=os.getenv("ADMIN_PASSWORD", ""),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./vicecity.db"),
            shop_id=os.getenv("SHOP_ID", "vice-city-auto"),
            shops_dir=os.getenv("SHOPS_DIR") or str(DEFAULT_SHOPS_DIR),
        )


STATUS_TEXT = {
    "waiting_on_parts": "waiting on parts",
    "in_progress": "being worked on right now",
    "ready_for_pickup": "ready for pickup",
    "awaiting_approval": "waiting for the customer's approval on the estimate",
    "diagnosing": "being diagnosed",
}


# Said only when the shop lists the language in callback_languages (docs/SHOPS.md).
STAFFED_CALLBACK = {
    "es": "Alguien que habla español le llamará.",
    "fr": "A French-speaking team member will call back.",
    "ht": "A Creole-speaking team member will call back.",
}

UNKNOWN_SHOP = {
    "ok": False,
    "error": "unknown_shop",
    "message": "I can't do that on this line. Offer to take a message so someone can call back.",
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
        value = "".join(" " if unicodedata.category(c) == "Cc" else c for c in value)
        return " ".join(value.split())
    return value


def integer_to_string(value):
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


def normalize_phone(value):
    value = integer_to_string(value)
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError("invalid phone")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
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


class ToolRequest(BaseModel):
    # Set by the agent's tool config (a dynamic variable), never by the LLM; omitted means SHOP_ID.
    shop_id: str | None = Field(default=None, pattern=SHOP_ID_PATTERN)


class BookAppointment(ToolRequest):
    customer_name: Name
    phone: Phone
    vehicle: Name = Field(description="year make model")
    service: Text = Field(description="what the customer needs")
    preferred_date: Annotated[str, BeforeValidator(validate_date)]
    preferred_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    language: Language = "en"
    notes: OptionalText | None = None


class RepairStatus(ToolRequest):
    phone: Annotated[str | None, BeforeValidator(integer_to_string)] = Field(default=None, max_length=32)
    ro_number: Annotated[str | None, BeforeValidator(integer_to_string)] = Field(default=None, max_length=20)
    last_name: str | None = Field(default=None, max_length=80)


class TowRequest(ToolRequest):
    customer_name: Name
    phone: Phone
    vehicle: Name
    pickup_location: Text = Field(description="street address, cross streets, or landmark")
    issue: Text = Field(description="what happened")
    is_safe: bool = Field(description="caller confirmed they are in a safe place")
    destination: OptionalText | None = None
    language: Language = "en"


class TakeMessage(ToolRequest):
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
        "phone": "I need a 10-digit phone number with area code.",
        "preferred_date": "I need the date as year-month-day, like 2026-11-20.",
        "preferred_time": "I need the time in 24-hour format, like 09:30 or 14:00.",
        "language": "Language must be en, es, fr or ht.",
        "callback_language": "Language must be en, es, fr or ht.",
        "urgency": "Urgency must be low, normal or high.",
        "is_safe": "I need to know if the caller is safe: true or false.",
        "shop_id": "The shop id is not valid; check the tool configuration.",
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

    def __init__(self, app, deadline_ms, default_shop_id):
        self.app = app
        self.deadline_seconds = deadline_ms / 1000
        self.default_shop_id = default_shop_id

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

        token = _REQUEST_DEADLINE.set(start + self.deadline_seconds if is_tool else None)
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
            _REQUEST_DEADLINE.reset(token)
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
            if result.get("error") in {"invalid_input", "timeout", "internal_error", "unknown_shop"}:
                error = result["error"]
                if error == "invalid_input":
                    entry["field"] = result["field"]
            if error:
                entry["error"] = error
            state = scope.get("state", {})
            tool_body = state.get("bear_body", {})
            if "bear_shop_id" in state:
                # Set by the init webhook after auth; the dialed and calling numbers are never logged.
                entry["shop_id"] = state["bear_shop_id"]
                if "conversation_id" not in entry and _CORRELATION_ID.fullmatch(state["bear_conversation_id"]):
                    entry["conversation_id"] = state["bear_conversation_id"]
            if is_tool:
                shop_id = tool_body.get("shop_id")
                shop_id = self.default_shop_id if shop_id is None else shop_id
                if isinstance(shop_id, str) and re.fullmatch(SHOP_ID_PATTERN, shop_id):
                    entry["shop_id"] = shop_id
            phone = tool_body.get("phone")
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
            await self.app(scope, receive, send)
            return
        # Transport checks run before FastAPI parses the body or checks the tool secret.
        headers = dict(scope["headers"])

        async def reject(status, detail):
            await JSONResponse(status_code=status, content={"detail": detail})(scope, receive, send)

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
    if settings.env != "dev" and len(settings.init_webhook_secret) < 32:
        raise RuntimeError(
            "INIT_WEBHOOK_SECRET must be set to at least 32 characters (or set BEAR_ENV=dev for local testing)"
        )
    if settings.init_webhook_secret and settings.init_webhook_secret == settings.tool_secret:
        raise RuntimeError("INIT_WEBHOOK_SECRET must differ from TOOL_SECRET")
    if not settings.init_webhook_secret:
        logging.getLogger("bear").warning("INIT_WEBHOOK_SECRET is unset; the init webhook is open in dev")
    if settings.post_call_webhook_secret in {settings.tool_secret, settings.init_webhook_secret} - {""}:
        raise RuntimeError("POST_CALL_WEBHOOK_SECRET must differ from TOOL_SECRET and INIT_WEBHOOK_SECRET")
    if not settings.post_call_webhook_secret:
        logging.getLogger("bear").warning("POST_CALL_WEBHOOK_SECRET is unset; post-call summaries are disabled")

    if not re.fullmatch(SHOP_ID_PATTERN, settings.shop_id):
        raise RuntimeError("invalid SHOP_ID")
    shops = load_shops(settings.shops_dir)
    if settings.shop_id not in shops:
        raise RuntimeError(f"SHOP_ID {settings.shop_id} has no shop file in SHOPS_DIR")
    default_shop = shops[settings.shop_id]
    engine = None
    try:
        engine = make_engine(settings.database_url)
        check_schema(engine)
    except Exception:
        if engine is not None:
            engine.dispose()
        raise RuntimeError("Database unavailable or schema missing; run alembic upgrade head") from None

    def check_deadline():
        deadline = _REQUEST_DEADLINE.get()
        if deadline is not None and time.monotonic() >= deadline:
            raise _DeadlineExceeded

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(lifespan=lifespan, title=f"{default_shop.name} AI Receptionist tools", version="0.1.0")
    app.state.settings = settings
    app.state.shops = shops
    shops_by_number = {shop.twilio_number: shop for shop in shops.values()}
    store = app.state.store = Store(engine, write_guard=check_deadline)
    app.add_middleware(ToolTransportMiddleware)
    app.add_middleware(
        RequestLoggingMiddleware, deadline_ms=settings.tool_deadline_ms, default_shop_id=settings.shop_id
    )

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

    def check_init_secret(x_init_webhook_secret: str | None = Header(default=None)):
        if settings.init_webhook_secret and not hmac.compare_digest(
            (x_init_webhook_secret or "").encode("utf-8"), settings.init_webhook_secret.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="bad init webhook secret")

    def digits(s: str | None) -> str:
        return re.sub(r"\D", "", s or "")[-10:]

    # ---------- helpers ----------
    def resolve_shop(shop_id: str | None) -> ShopConfig | None:
        return shops.get(settings.shop_id if shop_id is None else shop_id)

    def slot_problem(shop: ShopConfig, dt: datetime) -> str | None:
        reason = shop.closed_reason(dt)
        if reason is None and dt < datetime.now(shop.tz):
            return "That time already passed."
        return reason

    def next_slots(shop: ShopConfig, start: datetime, n: int = 3) -> list[str]:
        out = []
        counts = store.booked_counts(shop.shop_id, start, start + timedelta(days=15))
        for t in shop.upcoming_slots(start, days=14):
            if counts.get(t.astimezone(UTC), 0) < shop.bays_per_slot:
                out.append(t.strftime("%A %b %d at %I:%M %p").replace(" 0", " "))
                if len(out) == n:
                    break
        return out

    # ---------- endpoints ----------
    @app.get("/health")
    def health():
        return {"ok": True, "time": datetime.now(default_shop.tz).isoformat()}

    @app.post("/elevenlabs/init", dependencies=[Depends(check_init_secret)])
    async def elevenlabs_init(request: Request):
        """Conversation-initiation webhook: pick the shop by the dialed number before the call connects."""
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16384:
                raise HTTPException(status_code=413, detail="request body too large")
        try:
            call = InitRequest.model_validate_json(bytes(raw))
        except ValidationError:
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
        try:
            number = normalize_phone(call.called_number)
        except ValueError:
            number = call.called_number
        shop = shops_by_number.get(number)
        request.state.bear_shop_id = UNKNOWN_SHOP_ID if shop is None else shop.shop_id
        request.state.bear_conversation_id = call.conversation_id
        now = datetime.now(UTC)
        if shop is None:
            reply = unknown_initiation(now, default_shop.tz)
        else:
            reply = shop_initiation(shop, now)
        return JSONResponse(reply.model_dump(exclude_none=True))

    @app.post("/elevenlabs/post-call")
    async def elevenlabs_post_call(request: Request):
        """Post-call webhook: keep a short, signed summary of each finished call (never the transcript)."""
        if not settings.post_call_webhook_secret:
            raise HTTPException(status_code=503, detail="post-call webhook disabled")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > POST_CALL_MAX_BYTES:
                raise HTTPException(status_code=413, detail="request body too large")
        try:
            verify_signature(
                bytes(raw),
                request.headers.get("elevenlabs-signature"),
                settings.post_call_webhook_secret,
                now=time.time(),
            )
        except SignatureError:
            raise HTTPException(status_code=401, detail="bad signature") from None
        try:
            event = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise HTTPException(status_code=400, detail="invalid event")
        if event["type"] != "post_call_transcription":
            return {"status": "ignored"}  # audio, call_initiation_failure, and future types
        if not isinstance(event.get("data"), dict):
            raise HTTPException(status_code=400, detail="invalid event")
        try:
            call = parse_transcription(event["data"])
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid conversation_id") from None
        shop_id = call.shop_id if call.shop_id in shops else UNKNOWN_SHOP_ID
        request.state.bear_shop_id = shop_id
        request.state.bear_conversation_id = call.fields["conversation_id"]
        saved = store.save_call_summary(shop_id, **call.fields)
        return {"status": "stored" if saved else "duplicate"}

    @app.post("/tools/book_appointment", dependencies=[Depends(check_secret)])
    def book_appointment(req: BookAppointment):
        shop = resolve_shop(req.shop_id)
        if shop is None:
            return UNKNOWN_SHOP
        if date.fromisoformat(req.preferred_date) > datetime.now(shop.tz).date() + timedelta(days=60):
            return invalid_input("preferred_date", "I can only book up to 60 days out.")
        dt = datetime.strptime(f"{req.preferred_date} {req.preferred_time}", "%Y-%m-%d %H:%M").replace(tzinfo=shop.tz)
        problem = slot_problem(shop, dt)
        if problem is None:
            fields = req.model_dump(exclude={"preferred_date", "preferred_time", "shop_id"})
            try:
                appt = store.book_appointment(shop.shop_id, shop.slot_start(dt), shop.bays_per_slot, **fields)
            except SlotFull:
                problem = "That slot is full."
        if problem:
            return {
                "ok": True,
                "booked": False,
                "message": problem,
                "alternatives": next_slots(shop, max(dt, datetime.now(shop.tz))),
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
        shop = resolve_shop(req.shop_id)
        if shop is None:
            return UNKNOWN_SHOP
        last_name = (req.last_name or "").strip()
        phone = (req.phone or "").strip()
        ro = (req.ro_number or "").strip()
        if not last_name or not (phone or ro):
            store.record_audit(shop.shop_id, "repair_status.lookup", outcome="needs_info")
            return REPAIR_STATUS_NEEDS

        names = {normalize_name(last_name), normalize_name(last_name.split()[-1])}
        phone_digits = re.sub(r"\D", "", phone)
        hits = []
        for record in store.list_repair_orders(shop.shop_id):
            customer_tokens = record["customer_name"].split()
            if not customer_tokens or normalize_name(customer_tokens[-1]) not in names:
                continue
            if phone and (len(phone_digits) < 10 or digits(record["phone"]) != phone_digits[-10:]):
                continue
            if ro and record["ro_number"] != ro:
                continue
            hits.append(record)
        if len(hits) != 1:
            store.record_audit(shop.shop_id, "repair_status.lookup", outcome="failed")
            return REPAIR_STATUS_FAILURE
        r = hits[0]
        store.record_audit(shop.shop_id, "repair_status.lookup", r["public_id"], outcome="verified")
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
        shop = resolve_shop(req.shop_id)
        if shop is None:
            return UNKNOWN_SHOP
        fields = req.model_dump(exclude={"shop_id"})
        fields["destination"] = req.destination or shop.name
        tow = store.create_tow(shop.shop_id, **fields, status="dispatch_requested")
        if shop.features.tow_dispatch:
            msg = "Tow request logged; dispatcher will call back within 15 minutes with an ETA."
        else:
            msg = f'Tow request logged. Tell the caller: "{TOW_FALLBACK_REPLY}"'
        if not req.is_safe:
            msg = "Caller may be unsafe: tell them to call 911 now if anyone is hurt or they're in traffic. " + msg
        return {"ok": True, "logged": True, "ticket": tow["public_id"], "message": msg}

    @app.post("/tools/take_message", dependencies=[Depends(check_secret)])
    def take_message(req: TakeMessage):
        # Calls to a number no shop owns (Task 8) may still leave a message, filed under "unknown".
        if req.shop_id == UNKNOWN_SHOP_ID:
            shop_id, staffed = UNKNOWN_SHOP_ID, ["en"]
        else:
            shop = resolve_shop(req.shop_id)
            if shop is None:
                return UNKNOWN_SHOP
            shop_id, staffed = shop.shop_id, shop.callback_languages
        m = store.create_message(shop_id, **req.model_dump(exclude={"vehicle", "shop_id"}))
        language = req.callback_language
        if language == "en":
            lang = ""
        elif language in staffed:
            lang = " " + STAFFED_CALLBACK[language]
        else:
            lang = (
                f" Don't promise a {LANGUAGE_NAMES[language]}-speaking callback; "
                "no one who speaks it is listed for this shop."
            )
        return {
            "ok": True,
            "saved": True,
            "ticket": m["public_id"],
            "message": "Message saved for the service writer. The shop will call back as soon as possible." + lang,
        }

    @app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(check_admin)])
    def admin(shop: str | None = None):
        """Live board for the requested shop."""
        shop = settings.shop_id if shop is None else shop
        if not re.fullmatch(SHOP_ID_PATTERN, shop):
            raise HTTPException(status_code=400, detail="invalid shop")
        if shop == UNKNOWN_SHOP_ID:
            # Messages from numbers no shop owns, shown in the default shop's time zone.
            board_name, board_tz = "Unrecognized numbers", default_shop.tz
        elif shop in shops:
            board_name, board_tz = shops[shop].name, shops[shop].tz
        else:
            raise HTTPException(status_code=404, detail="unknown shop")
        db = store.board(shop, 50)

        def display(value):
            if isinstance(value, datetime):
                return value.astimezone(board_tz).strftime("%Y-%m-%d %H:%M %Z")
            return str(value)

        def table(rows, cols):
            h = "".join(f"<th>{html.escape(str(c), quote=True)}</th>" for c in cols)
            b = "".join(
                "<tr>" + "".join(f"<td>{html.escape(display(r.get(c, '')), quote=True)}</td>" for c in cols) + "</tr>"
                for r in rows
            )
            return f"<table><tr>{h}</tr>{b}</table>"

        return f"""<html><head><meta http-equiv="refresh" content="3"><title>{html.escape(str(board_name), quote=True)} board</title>
    <style>body{{font-family:system-ui;margin:24px}}table{{border-collapse:collapse;margin-bottom:24px}}
    td,th{{border:1px solid #ccc;padding:6px 10px}}th{{background:#222;color:#fff}}</style></head><body>
    <h1>{html.escape(str(board_name), quote=True)}: AI receptionist live board</h1>
    <h2>Appointments</h2>{table(db["appointments"], ["public_id", "slot_start", "customer_name", "phone", "vehicle", "service", "language"])}
    <h2>Tow requests</h2>{table(db["tows"], ["public_id", "created_at", "customer_name", "phone", "pickup_location", "issue", "is_safe"])}
    <h2>Messages</h2>{table(db["messages"], ["public_id", "created_at", "caller_name", "phone", "reason", "urgency", "callback_language"])}
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
