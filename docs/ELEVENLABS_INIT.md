# Call-start webhook (Task 8)

ElevenLabs calls `POST /elevenlabs/init` while an inbound Twilio call is still ringing. The backend uses the number that was dialed to find the shop, then returns that shop's greeting, language, voice and prompt variables. The call never has to hard-code a shop.

## Request
- Header: `X-Init-Webhook-Secret: <INIT_WEBHOOK_SECRET>`, checked with a constant-time compare. If it's missing or wrong, the backend returns `401`. `TOOL_SECRET` is not accepted here. During rotation, `INIT_WEBHOOK_SECRET_PREVIOUS` is also accepted so you can update ElevenLabs and the server in either order.
- Body (JSON, 16 KB max): `caller_id`, `called_number`, `agent_id`, `call_sid`, `conversation_id`. All of them are strings and all are optional. Unknown fields, such as `call_id` and `sip_headers` on SIP calls, are ignored. Invalid JSON, or a non-string value, returns `400`. A body over 16 KB returns `413`.
- `called_number` is normalized to E.164 and matched against each shop's `twilio_number`.

## Response (`200`)
```json
{
  "type": "conversation_initiation_client_data",
  "dynamic_variables": {
    "shop_id": "vice-city-auto",
    "shop_name": "Vice City Auto",
    "shop_city": "Miami",
    "shop_address": "555 Vice Boulevard, Miami, FL 33139",
    "hours_text": "Monday to Friday 8:00 AM to 6:00 PM; Saturday 9:00 AM to 2:00 PM; closed Sunday. Closed for holidays on ...",
    "services_text": "general repair, diagnostics and check engine light, ...",
    "persona_name": "Bear",
    "features_text": "We do not send text confirmations. Tow requests go to staff; there is no automatic dispatch. After logging a tow, say: \"I've logged it and the shop will call you back as soon as possible. If you're unsafe, call 911.\" We cannot receive photos by text. Tell callers to bring photos to the estimate, ...",
    "shop_timezone": "America/New_York",
    "languages_text": "English, Spanish and French",
    "call_start_local": "Sunday, November 15, 2026, 09:30 AM"
  },
  "conversation_config_override": {
    "agent": {"first_message": "<the shop's first_messages for its first language>", "language": "en"},
    "tts": {"voice_id": "<the shop's voice for that language>"}
  }
}
```
The language used is the first one in the shop's `languages` list. `bear.initiation.InitResponse` is the Pydantic model of this shape, and the tests validate every response against it.

`call_start_local` is the shop's local time when the call started. The prompt uses it instead of `{{system__time}}`, which follows the agent's single configured time zone (not each shop's) and changes every turn, which defeats prompt caching. Unknown numbers get the default shop's time zone.

**Unknown number.** If no shop owns the dialed number, the response uses `shop_id: "unknown"` and neutral wording (no shop name). The English greeting includes the recording notice, there is no voice override, and `features_text` tells the agent it can only take a message. On those calls:
- `take_message` files messages under `unknown`, and they show up at `/admin?shop=unknown`. The tool also accepts a `conversation_id` dynamic variable (not filled by the LLM) so staff can find the call in the ElevenLabs dashboard without storing the dialed number.
- Every other tool returns `unknown_shop` and writes nothing.

**Timing.** ElevenLabs fetches this during the ring, and a failed or slow webhook can keep the call from starting. Our target is under 300 ms. The handler only does an in-memory lookup, and a test keeps 30 in-process calls under 300 ms each.

## Differences from the ElevenLabs docs (checked Nov 15, 2026)
Sources: [Twilio personalization](https://elevenlabs.io/docs/agents-platform/customization/personalization/twilio-personalization), [Personalization](https://elevenlabs.io/docs/agents-platform/customization/personalization) and [Dynamic variables](https://elevenlabs.io/docs/agents-platform/customization/personalization/dynamic-variables).
- The request fields match the docs. The docs add that SIP calls may include `call_id` and `sip_headers`, and that `call_sid` is an empty string outside telephony. We ignore the first two and accept an empty `call_sid`.
- The response uses only part of the documented shape. We don't send the optional `prompt`/`llm`, `conversation`, `user_id`, `branch_id` or `environment` overrides.
- The docs say `dynamic_variables` must contain **every custom dynamic variable defined on the agent**. We always send these eleven. If you add another variable to the agent, add it to `DynamicVariables` as well. `system__*` variables can't be set this way, and we don't try.
- The docs give no exact timeout. They only say to respond "within a reasonable timeout", and the response body must be 256 KB or smaller. The 300 ms target is our own.
- The header name is our choice, since the docs leave header secrets up to you.

## Manual setup in ElevenLabs (needs Israel's account)
1. **Agents settings → Conversation initiation webhook:** set the URL to `https://<backend host>/elevenlabs/init`. Add a header secret named `X-Init-Webhook-Secret` with the same value as `INIT_WEBHOOK_SECRET`, and store it in the ElevenLabs secrets manager.
2. **Agent → Security tab:**
   - Enable **Fetch initiation client data from a webhook**.
   - Enable the **first message**, **language** and **voice** overrides.
   - Don't enable the system prompt override. We don't send it.
3. **Agent → Dynamic variables:** add placeholders for `shop_id`, `shop_name`, `shop_city`, `shop_address`, `hours_text`, `services_text`, `persona_name`, `features_text`, `shop_timezone`, `languages_text` and `call_start_local`. Use `vice-city-auto` and the demo values. Preview and simulation tests don't call the webhook, so without placeholders those variables stay as literal `{{...}}` text.
4. **Tools:** re-create the four webhook tools from the regenerated `tools/*.json`. Each now has a required `shop_id` body parameter whose value type is **Dynamic variable** `shop_id`, so the LLM never fills it in.
5. **Confirm on the live agent:** after re-creating the tools, open each tool's parameter list in the ElevenLabs UI and check that `shop_id` is a dynamic variable, not an LLM-filled field.
