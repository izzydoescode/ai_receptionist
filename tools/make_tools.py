"""Writes ElevenLabs webhook tool configs to tools/*.json.
Usage: python tools/make_tools.py https://YOUR-ID.ngrok-free.app
Each file is a valid `tool_config` for POST /v1/convai/tools (or `elevenlabs tools add --config-path`).
"""

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_BASE = "https://YOUR-NGROK-ID.ngrok-free.app"


def S(d):
    return {"type": "string", "description": d}


def B(d):
    return {"type": "boolean", "description": d}


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


def main(argv=None, out_dir=None):
    out = Path(out_dir) if out_dir is not None else TOOLS_DIR
    base = (argv[0] if argv else DEFAULT_BASE).rstrip("/")
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
                "request_headers": {"Content-Type": "application/json"},
                "request_body_schema": {
                    "type": "object",
                    "description": f"Arguments for {name}",
                    "properties": props,
                    "required": req,
                },
            },
        }
        (out / f"{name}.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        paths.append(out / f"{name}.json")
        print("wrote", paths[-1])
    return paths


if __name__ == "__main__":
    main(sys.argv[1:])
