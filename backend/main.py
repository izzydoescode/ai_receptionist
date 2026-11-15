"""
Shop Receptionist tool backend for an ElevenLabs agent.

ElevenLabs "webhook" tools POST JSON bodies to these endpoints during a call,
and the agent reads the JSON reply back to the caller.

Run:  uvicorn --factory main:create_app --reload --port 8000
Expose: ngrok http 8000   (then paste https://<id>.ngrok-free.app/tools/... into each tool)
"""
from __future__ import annotations

import base64
import binascii
import hmac
import html
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class Settings:
    tool_secret: str
    env: str
    admin_password: str
    data_file: Path
    admin_user: str = "admin"
    shop_name: str = "Vice City Auto"
    shop_tz: ZoneInfo = ZoneInfo("America/New_York")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
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


# ---------- models ----------
class BookAppointment(BaseModel):
    customer_name: str
    phone: str
    vehicle: str = Field(description="year make model")
    service: str = Field(description="what the customer needs, e.g. oil change, brakes, collision estimate")
    preferred_date: str = Field(description="YYYY-MM-DD")
    preferred_time: str = Field(description="HH:MM 24h, shop local time")
    language: str = "en"
    notes: Optional[str] = None


class RepairStatus(BaseModel):
    phone: Optional[str] = None
    ro_number: Optional[str] = None
    plate: Optional[str] = None
    last_name: Optional[str] = None


class TowRequest(BaseModel):
    customer_name: str
    phone: str
    vehicle: str
    pickup_location: str = Field(description="street address, cross streets, or landmark")
    issue: str = Field(description="what happened: won't start, accident, flat, overheating...")
    is_safe: bool = Field(description="caller confirmed they are in a safe place")
    destination: Optional[str] = None
    language: str = "en"


class TakeMessage(BaseModel):
    caller_name: str
    phone: str
    reason: str
    urgency: str = Field(default="normal", description="low | normal | high")
    callback_language: str = Field(default="en", description="en | es | ht | fr")
    vehicle: Optional[str] = None


def create_app() -> FastAPI:
    settings = Settings.from_env()
    if settings.env != "dev" and len(settings.tool_secret) < 32:
        raise RuntimeError("TOOL_SECRET must be set to at least 32 characters (or set BEAR_ENV=dev for local testing)")
    if not settings.tool_secret:
        logging.getLogger("bear").warning("TOOL_SECRET is unset; tool authentication is disabled in dev")

    app = FastAPI(title=f"{settings.shop_name} AI Receptionist tools", version="0.1.0")
    app.state.settings = settings

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
        if settings.data_file.exists():
            return json.loads(settings.data_file.read_text())
        save(SEED)
        return json.loads(json.dumps(SEED))

    def save(db: dict) -> None:
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
        db = load()
        try:
            dt = datetime.strptime(f"{req.preferred_date} {req.preferred_time}", "%Y-%m-%d %H:%M").replace(tzinfo=settings.shop_tz)
        except ValueError:
            return {"booked": False, "message": "I need the date as YYYY-MM-DD and time as HH:MM.",
                    "alternatives": next_slots(db, datetime.now(settings.shop_tz))}
        problem = slot_ok(dt) or ("That slot is full." if count_at(db, dt) >= BAYS_PER_SLOT else None)
        if problem:
            return {"booked": False, "message": problem, "alternatives": next_slots(db, max(dt, datetime.now(settings.shop_tz)))}
        appt = {"id": "A" + uuid.uuid4().hex[:6].upper(), "start": dt.isoformat(), **req.model_dump(),
                "created": datetime.now(settings.shop_tz).isoformat()}
        db["appointments"].append(appt)
        save(db)
        return {"booked": True, "confirmation": appt["id"],
                "when": dt.strftime("%A %b %d at %I:%M %p").replace(" 0", " "),
                "message": f"Booked {req.vehicle} for {req.service}. Bring the keys to the front desk."}

    @app.post("/tools/check_repair_status", dependencies=[Depends(check_secret)])
    def check_repair_status(req: RepairStatus):
        db = load()
        p, ro = digits(req.phone), (req.ro_number or "").strip()
        plate = (req.plate or "").replace(" ", "").upper()
        ln = (req.last_name or "").strip().lower()
        hits = [r for r in db["repairs"] if
                (ro and r["ro"] == ro) or (p and digits(r["phone"]) == p) or
                (plate and r["plate"] == plate) or (ln and r["customer"].lower().split()[-1] == ln)]
        if not hits:
            return {"found": False, "message": "No open repair order matches. Offer to take a message for the service writer."}
        r = hits[0]
        return {"found": True, "ro_number": r["ro"], "customer_first_name": r["customer"].split()[0],
                "vehicle": r["vehicle"], "status": STATUS_TEXT.get(r["status"], r["status"]),
                "details": r["note"], "eta": r["eta"], "estimated_balance": r["balance_estimate"],
                "verify": "Confirm the caller's first name matches before sharing details or price."}

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
        return {"logged": True, "ticket": tow["id"], "message": msg}

    @app.post("/tools/take_message", dependencies=[Depends(check_secret)])
    def take_message(req: TakeMessage):
        db = load()
        m = {"id": "M" + uuid.uuid4().hex[:6].upper(), **req.model_dump(), "created": datetime.now(settings.shop_tz).isoformat()}
        db["messages"].append(m)
        save(db)
        lang = {"ht": " A Creole-speaking team member will call back.", "es": " Alguien que habla español le llamará."}.get(req.callback_language, "")
        return {"saved": True, "ticket": m["id"], "message": "Message saved for the service writer." + lang}

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
