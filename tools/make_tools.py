"""Writes ElevenLabs webhook tool configs to tools/*.json.
Usage: PUBLIC_BASE_URL=https://your-backend.example python tools/make_tools.py
Each file is a valid `tool_config` for POST /v1/convai/tools (or `elevenlabs tools add --config-path`).
See tools/README.md for the manual steps.
"""

import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_BASE = "https://YOUR-BACKEND-HOST"
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
DEFAULT_SECRET_ID = "YOUR-TOOL-SECRET-ID"


def public_base_url(environ):
    """Validated PUBLIC_BASE_URL without a trailing slash: https, or http only for a local host."""
    base = (environ.get("PUBLIC_BASE_URL") or DEFAULT_BASE).rstrip("/")
    parts = urlsplit(base)
    secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS)
    if not secure or not parts.hostname or parts.path or parts.query or parts.fragment or parts.username:
        raise SystemExit(
            "PUBLIC_BASE_URL must be an https:// origin like https://bear.example.com "
            "(no path, query or credentials; http:// only for localhost)"
        )
    return base


def S(d):
    return {"type": "string", "description": d}


def B(d):
    return {"type": "boolean", "description": d}


# Filled by ElevenLabs from the shop_id dynamic variable the init webhook returns (Task 8).
# A property with dynamic_variable and no description is never shown to the LLM.
SHOP_ID = {"type": "string", "dynamic_variable": "shop_id"}


TOOLS = {
    "book_appointment": (
        "Book a drop-off appointment at the shop. Call ONLY after you have collected and read back name, phone, vehicle, service, date and time. If it returns booked=false, offer the alternatives it gives.",
        {
            "customer_name": S("Caller's full name"),
            "phone": S(
                "Callback phone number, digits only with area code. Default to the caller ID if they confirm it."
            ),
            "vehicle": S("Year, make and model, e.g. 2017 Chevy Malibu"),
            "service": S(
                "What they need: oil change, brakes, A/C, check engine light, collision/body estimate, tires, etc."
            ),
            "preferred_date": S(
                "Date in YYYY-MM-DD. Resolve words like 'tomorrow' using the current date in the system prompt."
            ),
            "preferred_time": S("Time in 24h HH:MM shop local time, e.g. 09:00 or 14:30"),
            "language": S("Language of the call: en, es, fr or ht"),
            "notes": S("Anything else useful: symptoms, insurance claim, needs a ride, etc."),
        },
        ["customer_name", "phone", "vehicle", "service", "preferred_date", "preferred_time"],
    ),
    "check_repair_status": (
        "Look up the status of a car that is already at the shop. Requires the customer's last name AND either the phone number on the ticket or the RO (repair order) number. Ask for both before calling. If found=false with 'needs', ask for what's missing; if it can't verify, offer to take a message.",
        {
            "last_name": S("Customer last name as on the repair order"),
            "phone": S("Phone number on the repair order, digits only"),
            "ro_number": S("Repair order (RO) / ticket number"),
        },
        ["last_name"],
    ),
    "request_tow": (
        "Log a tow/roadside request for the dispatcher. Use for breakdowns, accidents, cars that won't start or are unsafe to drive. Ask about safety FIRST.",
        {
            "customer_name": S("Caller's name"),
            "phone": S("Callback number, digits only"),
            "vehicle": S("Year make model and color"),
            "pickup_location": S("Exact location: address, cross streets, highway and exit, or landmark"),
            "issue": S("What happened"),
            "is_safe": B("True only if the caller confirmed they and passengers are safe and out of traffic"),
            "destination": S("Where to tow it; default is our shop"),
            "language": S("Language of the call: en, es, fr or ht"),
        },
        ["customer_name", "phone", "vehicle", "pickup_location", "issue", "is_safe"],
    ),
    "take_message": (
        "Leave a message for the service writer/owner: after hours, estimate requests needing a human, unknown questions, failed transfers, or Haitian Creole callers who want a Creole-speaking callback.",
        {
            "caller_name": S("Caller's name"),
            "phone": S("Callback number, digits only"),
            "reason": S("Short summary of what they need, in English"),
            "urgency": S("low, normal or high"),
            "callback_language": S("Language for the callback: en, es, ht (Haitian Creole) or fr"),
            "vehicle": S("Year make model if relevant"),
        },
        ["caller_name", "phone", "reason"],
    ),
}


def tool_secret_id(environ):
    """ID of the ElevenLabs workspace secret holding TOOL_SECRET; the value itself never goes in JSON."""
    secret_id = environ.get("ELEVENLABS_TOOL_SECRET_ID") or DEFAULT_SECRET_ID
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", secret_id):
        raise SystemExit("ELEVENLABS_TOOL_SECRET_ID must be the secret's id (letters, digits, - and _)")
    tool_secret = environ.get("TOOL_SECRET", "")
    if tool_secret and hmac.compare_digest(secret_id.encode(), tool_secret.encode()):
        raise SystemExit("ELEVENLABS_TOOL_SECRET_ID is the secret value itself; use the id ElevenLabs shows for it")
    return secret_id


def main(out_dir=None, environ=None):
    out = Path(out_dir) if out_dir is not None else TOOLS_DIR
    environ = os.environ if environ is None else environ
    base = public_base_url(environ)
    headers = {"Content-Type": "application/json", "X-Tool-Secret": {"secret_id": tool_secret_id(environ)}}
    paths = []
    for name, (desc, props, req) in TOOLS.items():
        cfg = {
            "type": "webhook",
            "name": name,
            "description": desc,
            "response_timeout_secs": 10,
            "api_schema": {
                "url": f"{base}/tools/{name}",
                "method": "POST",
                "request_headers": headers,
                "request_body_schema": {
                    "type": "object",
                    "description": f"Arguments for {name}",
                    "properties": {**props, "shop_id": SHOP_ID},
                    "required": [*req, "shop_id"],
                },
            },
        }
        (out / f"{name}.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        paths.append(out / f"{name}.json")
        print("wrote", paths[-1])
    return paths


if __name__ == "__main__":
    main()
