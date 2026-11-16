"""ElevenLabs conversation-initiation webhook payloads.

Shapes follow https://elevenlabs.io/docs/agents-platform/customization/personalization
(request: caller_id, called_number, agent_id, call_sid, conversation_id; response:
conversation_initiation_client_data with dynamic_variables and conversation_config_override).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bear.shops import UNKNOWN_SHOP_ID, ShopConfig

UNKNOWN_FIRST_MESSAGE = (
    "Thanks for calling. This is the shop's virtual assistant. Calls may be recorded. "
    "I can take a message and have someone call you back. May I have your name?"
)


class InitRequest(BaseModel):
    # SIP calls add call_id and sip_headers; unknown fields are ignored, not rejected.
    model_config = ConfigDict(extra="ignore")

    caller_id: str = Field(default="", max_length=256)
    called_number: str = Field(default="", max_length=256)
    agent_id: str = Field(default="", max_length=256)
    call_sid: str = Field(default="", max_length=256)
    conversation_id: str = Field(default="", max_length=256)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DynamicVariables(_Strict):
    shop_id: str
    shop_name: str
    shop_city: str
    shop_address: str
    hours_text: str
    services_text: str
    persona_name: str
    features_text: str


class AgentOverride(_Strict):
    first_message: str
    language: str


class TtsOverride(_Strict):
    voice_id: str


class ConfigOverride(_Strict):
    agent: AgentOverride
    tts: TtsOverride | None = None


class InitResponse(_Strict):
    type: Literal["conversation_initiation_client_data"] = "conversation_initiation_client_data"
    dynamic_variables: DynamicVariables
    conversation_config_override: ConfigOverride


def shop_initiation(shop: ShopConfig, today: date) -> InitResponse:
    """Greeting, voice and prompt variables for a call to ``shop``'s number, in its primary language."""
    language = shop.languages[0]
    return InitResponse(
        dynamic_variables=DynamicVariables(
            shop_id=shop.shop_id,
            shop_name=shop.name,
            shop_city=shop.city,
            shop_address=shop.address,
            hours_text=shop.hours_text(today),
            services_text=shop.services_text(),
            persona_name=shop.persona.name,
            features_text=shop.features_text(),
        ),
        conversation_config_override=ConfigOverride(
            agent=AgentOverride(first_message=shop.first_messages[language], language=language),
            tts=TtsOverride(voice_id=shop.voices[language]),
        ),
    )


def unknown_initiation() -> InitResponse:
    """Safe, shop-neutral greeting for a number no shop owns. Tools only take messages in this state."""
    return InitResponse(
        dynamic_variables=DynamicVariables(
            shop_id=UNKNOWN_SHOP_ID,
            shop_name="the shop",
            shop_city="the area",
            shop_address="not available on this line",
            hours_text="Hours are not available on this line.",
            services_text="not available on this line",
            persona_name="the virtual assistant",
            features_text=(
                "This line can only take a message. Do not book appointments, check repair status or log tows; "
                "take a message with take_message so someone can call back."
            ),
        ),
        # No voice override: the agent's default voice is used.
        conversation_config_override=ConfigOverride(
            agent=AgentOverride(first_message=UNKNOWN_FIRST_MESSAGE, language="en")
        ),
    )
