# Shop config (Task 7)

Each shop is one YAML file, `shops/<shop_id>.yaml`. The app loads every `*.yaml` file in `SHOPS_DIR` (default: the repo's `shops/` folder) when it starts. If any file is invalid, startup stops with one error that lists every problem, file by file, like this:

```
Invalid shop config in shops:
- vice-city-auto.yaml: hours.monday[0]: close must be after open
- vice-city-auto.yaml: first_messages.es must include the recording notice "puede ser grabada"
- twilio_number +13055550100 is used by both a-shop.yaml and b-shop.yaml
```

The errors show where the problem is and what's wrong, but never the value you entered. `shops/vice-city-auto.yaml` is the made-up demo shop. Copy it to start a new one, and never commit a real shop's details without the owner's OK.

## Fields

| Field | Required | Rules |
|---|---|---|
| `shop_id` | yes | `^[a-z0-9][a-z0-9-]{0,63}$`, and it must match the file name. `unknown` is reserved for calls to numbers no shop owns |
| `name`, `city` | yes | 1-80 characters |
| `address` | yes | 1-200 characters |
| `timezone` | yes | IANA name, e.g. `America/New_York` |
| `twilio_number` | yes | E.164 (`+13055550100`), unique across all shops (Task 8 finds the shop from the number that was dialed) |
| `transfer_number` | yes | E.164, different from `twilio_number` |
| `hours` | yes | All seven weekdays, `monday` to `sunday`. Each one is a list of `{open: "HH:MM", close: "HH:MM"}` in order with no overlaps, and an empty list `[]` means closed. **Quote the times**, because YAML reads an unquoted `18:00` as a number |
| `holidays` | no | List of dates (`2026-12-25`). The shop is closed all day on each one |
| `bays_per_slot` | yes | 1-50 bookings per slot |
| `slot_minutes` | yes | 5-480. Slots start at each `open` time, and the last one has to end by `close` |
| `services` | yes | At least one, 1-80 characters each |
| `languages` | yes | Unique subset of `en`, `es`, `fr` |
| `ht_fallback` | no | What happens when a caller speaks Haitian Creole: `message` (default) takes a message, `french` switches to French and needs `fr` in `languages` |
| `voices` | yes | One ElevenLabs voice id for each language in `languages`, and no others |
| `first_messages` | yes | One greeting for each language in `languages`, and no others. Each greeting has to include that language's recording notice (case and accents are ignored): English `may be recorded`; Spanish `puede ser grabada`, `pueden ser grabadas`, or `se puede grabar`; French `peut être enregistré`. Florida law requires every party's consent to record a call (Fla. Stat. 934.03) |
| `features` | no | `sms_confirmations`, `tow_dispatch`, `photo_texting`. All default to `false`. Turn a flag on only when the shop really does that: the flags only change what the agent promises, they do not send texts, dispatch a tow, or accept photo texts |
| `persona` | yes | `name`, plus an optional `style` |

Unknown keys are rejected at every level, so a typo fails at startup instead of being silently ignored.

## How the app uses it
- `SHOP_ID` is the default shop, and it needs a file in `SHOPS_DIR`. `/health` uses its time zone.
- Every tool body accepts an optional `shop_id`. ElevenLabs fills it in from the `shop_id` dynamic variable that the call-start webhook returns, so the LLM never chooses it (see `docs/ELEVENLABS_INIT.md`). It may be omitted only in single-shop dev, where the tool uses `SHOP_ID`. If more than one shop is loaded, or the app is not in dev, a missing `shop_id` is `invalid_input` so a second shop's calls cannot silently write to the default shop. `shop_id: "unknown"` can only use `take_message`. If the id is malformed, the tool returns `invalid_input` on `shop_id`. If it's well-formed but has no file, the tool returns `200 {"ok": false, "error": "unknown_shop", ...}` and writes nothing.
- Booking follows that shop's hours, holidays, `slot_minutes` and `bays_per_slot`, in the shop's time zone. A requested time must start a slot (off-grid times are `invalid_input`). The tow destination defaults to the shop's `name`.
- `/admin?shop=<id>` shows that shop's name and timestamps in that shop's time zone. A shop with no file returns 404.
- Tool log lines include `shop_id`.
- Tows and messages are stored and shown on `/admin` (the page refreshes every 3 seconds). There is no staff email or SMS alert yet, so a callback promise holds only while a person is watching the board.

