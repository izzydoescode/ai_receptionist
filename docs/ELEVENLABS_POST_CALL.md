# ElevenLabs post-call webhook

After each call ends and ElevenLabs finishes its analysis, it posts the result to Bear. Bear keeps a short summary of the call for the shop's `/admin` board.

## Endpoint

- `POST /elevenlabs/post-call`. The endpoint is off (returns `503`) until `POST_CALL_WEBHOOK_SECRET` is set.
- **Signature:** ElevenLabs signs each request with HMAC. The header is `ElevenLabs-Signature: t=<unix seconds>,v0=<hex>`, where `v0` is HMAC-SHA256 of `"<t>.<raw body>"` using the webhook secret. Bear checks it without the ElevenLabs SDK (`backend/bear/webhook_signature.py`), using a constant-time compare.
  - Bear rejects timestamps older than 30 minutes (the SDK's limit) or more than 5 minutes in the future.
  - Any matching `v0` is accepted, so a secret can be rotated.
  - A bad, missing or stale signature gets `401`. The body isn't parsed until the signature checks out.
- **Body:** capped at 2 MB (`413` above that). A signed body that isn't a JSON event, or a transcription without a valid `conversation_id`, gets `400`.
- **Responses** (all `200`):

  | Event `type` | Response | What happens |
  |---|---|---|
  | `post_call_transcription` | `{"status": "stored"}` | The call summary is saved. |
  | `post_call_transcription` sent again (a retry) | `{"status": "duplicate"}` | Nothing changes. |
  | `post_call_audio`, `call_initiation_failure`, anything new | `{"status": "ignored"}` | Nothing is stored. |

  ElevenLabs disables a webhook after repeated failures, so retries and event types Bear doesn't use still get `200`.

## What is stored

There's one row in `call_summaries` per conversation (`conversation_id` is unique), filed under the `shop_id` dynamic variable that `/elevenlabs/init` set. If that variable is missing or names an unknown shop, the row goes to the `unknown` board.

| Column | Source in the payload |
|---|---|
| `conversation_id` | `data.conversation_id` |
| `started_at` | `data.metadata.start_time_unix_secs` (UTC) |
| `duration_secs` | `data.metadata.call_duration_secs` |
| `outcome` | `data.analysis.call_successful`: `success`, `failure` or `unknown` |
| `summary` | `data.analysis.transcript_summary`, trimmed to 2000 characters |
| `title` | `data.analysis.call_summary_title`, trimmed to 200 characters |
| `language` | `data.metadata.main_language`, falling back to the agent language override |

**Not stored:** the transcript, audio, caller and shop phone numbers, the cost, tool calls, and every other dynamic variable. They're dropped while parsing (`backend/bear/post_call.py`), so they never reach the database. Request logs carry only `shop_id` and `conversation_id`, never the summary.

The summary is ElevenLabs' own text, so it can mention what the caller said, such as their name or vehicle. It's shown only on the password-protected board.

Transcripts and audio stay in ElevenLabs under the agent's retention settings. A per-shop option to keep full transcripts in Bear isn't built yet.

## Setup (manual)

1. **ElevenLabs → Agents settings → Post-call webhook:** create a webhook pointing at `https://<backend host>/elevenlabs/post-call`, with HMAC authentication.
2. Turn on **transcription** events. Leave **Send audio data** off: Bear ignores audio, and audio bodies can go over the 2 MB limit.
3. Copy the generated shared secret into `POST_CALL_WEBHOOK_SECRET` on the backend. It must differ from `TOOL_SECRET` and `INIT_WEBHOOK_SECRET`. Restart, then check the board after a test call.
4. Optional: turn on webhook retries. They're safe, because a repeated conversation id is ignored.

The test fixtures in `tests/fixtures/elevenlabs/` follow the example payloads in the ElevenLabs post-call webhook docs, with demo data.
