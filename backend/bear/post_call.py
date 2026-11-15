"""Turn an ElevenLabs ``post_call_transcription`` payload into the few fields Bear keeps.

Only the conversation id, start time, duration, outcome, short summary, title, and language are
kept. The transcript, audio, phone numbers, and other dynamic variables are dropped here, so they
never reach the database or the logs. The payload grows over time, so unknown keys and odd value
types are ignored rather than rejected.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

CONVERSATION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
LANGUAGE = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,4})?")
OUTCOMES = frozenset({"success", "failure", "unknown"})
MAX_SUMMARY = 2000
MAX_TITLE = 200


@dataclass(frozen=True)
class CallSummary:
    shop_id: str | None  # as sent in the shop_id dynamic variable; the caller checks it is a known shop
    fields: dict


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return int(value)


def _text(value, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _language(*candidates) -> str | None:
    for value in candidates:
        if isinstance(value, str) and LANGUAGE.fullmatch(value):
            return value
    return None


def parse_transcription(data: dict) -> CallSummary:
    """Raise ValueError if the payload has no usable conversation id."""
    conversation_id = data.get("conversation_id")
    if not isinstance(conversation_id, str) or not CONVERSATION_ID.fullmatch(conversation_id):
        raise ValueError("conversation_id")
    metadata = _dict(data.get("metadata"))
    analysis = _dict(data.get("analysis"))
    client_data = _dict(data.get("conversation_initiation_client_data"))
    variables = _dict(client_data.get("dynamic_variables"))
    agent_override = _dict(_dict(client_data.get("conversation_config_override")).get("agent"))
    started = _number(metadata.get("start_time_unix_secs"))
    outcome = analysis.get("call_successful")
    shop_id = variables.get("shop_id")
    return CallSummary(
        shop_id=shop_id if isinstance(shop_id, str) else None,
        fields={
            "conversation_id": conversation_id,
            "started_at": None if started is None else datetime.fromtimestamp(started, UTC),
            "duration_secs": _number(metadata.get("call_duration_secs")),
            "outcome": outcome if isinstance(outcome, str) and outcome in OUTCOMES else "unknown",
            "summary": _text(analysis.get("transcript_summary"), MAX_SUMMARY) or "",
            "title": _text(analysis.get("call_summary_title"), MAX_TITLE),
            "language": _language(metadata.get("main_language"), agent_override.get("language")),
        },
    )
