# ElevenLabs agent config notes

Sources: ElevenLabs docs (Language, Language detection, Webhook tools, Transfer to number, Twilio native integration, Dynamic variables), checked Nov 14, 2026. Links in README.

## Core settings
| Setting | Value |
|---|---|
| Primary language | English |
| Additional languages | Spanish (es), French (fr). Haitian Creole isn't available, see README |
| TTS model | Default for new agents (Eleven v4 Turbo per the help center). Docs say adding languages moves the agent to a multilingual model; leave the default unless latency is bad |
| LLM | A strong tool-calling model (docs recommend "high intelligence" models for tools). Test latency on a real phone call |
| System tools | `language_detection` (on), `end_call` (on), `transfer_to_number` (Twilio **warm** transfer to the shop owner's cell), `voicemail_detection` optional |
| Webhook tools | 4 tools from `tools/*.json` |
| Timezone | America/New_York (so `{{system__time}}` is local) |
| Turn taking | Callers are in loud shops/cars: keep interruptions enabled; tune turn timeout ~1s |
| Knowledge base | Upload a 1-page "shop facts" doc (hours, address, services, warranty, payment, loaner policy) |
| Post-call | Turn on transcripts/analysis; demo the call log in Calls History |

## Voices (pick in Voice Library or make with Voice Design)
The kit doesn't name specific library voices: the library changes constantly and voice IDs vary by account, so audition them yourself (~10 min).

**How to pick fast:** Voices > Voice Library > filter by Language + Accent + Use case "Conversational" + Gender, sort by trending, then audition each with the same line:
"Yeah, your Malibu's waitin' on a wheel bearing. Should be ready tomorrow by three."
For each additional language, pick a voice whose native language matches. The ElevenLabs Language doc recommends a language-specific voice per language and links "language top picks."

**Voice Design prompts** (Voices > Create > Voice Design, generates 3 previews from text; costs credits):
1. **Bear (gruff shop guy, EN primary):** "Middle-aged American man from Brooklyn, New York. Gravelly, slightly raspy low voice, relaxed and confident, friendly mechanic who's seen it all. Medium pace, phone-call clarity, no background noise."
2. **Front desk (friendly, alt persona):** "Warm, upbeat woman in her 30s from Miami, clear American accent with a light Latina warmth, professional receptionist, smiling tone, medium-fast pace."
3. **Spanish:** "Hombre caribeño de unos 45 años, voz grave y amable, acento cubano/puertorriqueño suave, habla claro por teléfono, tono de mecánico de confianza." Assign to the `es` preset.
4. **French (Creole fallback):** "Haitian man in his 40s speaking French with a warm Haitian accent, calm, reassuring, clear phone delivery." Assign to the `fr` preset. (Voice Design describes accent; this doesn't make it speak Kreyòl.)

**Fun demo trick:** create 2 agents (Bear the gruff guy and "Gaby" the front desk) sharing the same tools and prompt with a different Personality block. Call both live.

## Webhook tool setup (per tool)
Full steps are in `tools/README.md`. In short: Agent > Tools > Add tool > Webhook, and copy the name, description, `POST` and URL (`<PUBLIC_BASE_URL>/tools/<name>`) from the generated JSON. Body parameters come from `request_body_schema.properties` and use the "LLM prompt" value type.
Required: Headers > `X-Tool-Secret` as a **Secret** that holds the backend's `TOOL_SECRET` (the backend rejects tool calls without it), and `X-Conversation-Id` as the dynamic variable `system__conversation_id`.
`shop_id` is the exception: its value type is **Dynamic variable** `shop_id`, filled by the call-start webhook (see `docs/ELEVENLABS_INIT.md` for that webhook and the Security-tab overrides).
Tip: on `phone` in book_appointment you can set the value type to the dynamic variable `system__caller_id` instead of asking.

## Transfer rule (transfer_to_number)
Condition: "Caller asks for a human twice, is upset, is an insurance adjuster/vendor, or has a complaint, and the shop is open."
Twilio: Warm transfer to owner's cell (E.164, e.g. +1954XXXXXXX). Warm = the human hears the agent's summary first.
