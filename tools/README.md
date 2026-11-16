# ElevenLabs tool configs

`tools/*.json` are the four webhook tools Bear calls: `book_appointment`, `check_repair_status`, `request_tow` and `take_message`. `make_tools.py` generates them. Don't edit the JSON by hand. Change `make_tools.py`, regenerate, and commit both.

## What the scripts need
| Env var | Script | Example | Used for |
|---|---|---|---|
| `PUBLIC_BASE_URL` | `make_tools.py` | `https://bear.example.com` | Each tool URL becomes `<PUBLIC_BASE_URL>/tools/<name>`. It must be an `https://` origin with no path. `http://` is only allowed for `localhost` or `127.0.0.1`. If unset, the URL is the placeholder `https://YOUR-BACKEND-HOST`. |
| `ELEVENLABS_TOOL_SECRET_ID` | `create_tools_via_api.py` | `sec_...` | The **id** of the ElevenLabs workspace secret that holds `TOOL_SECRET`. The script adds it to each config as `{"secret_id": ...}` in memory, just before uploading, and refuses to run if it equals `TOOL_SECRET`. |
| `ELEVENLABS_API_KEY` | `create_tools_via_api.py` | | API auth. |

On disk, `X-Tool-Secret` is always `{"secret_id": "YOUR-TOOL-SECRET-ID"}`, so no secret value or secret id is ever written to a file. `create_tools_via_api.py` refuses to upload configs that still have the placeholder URL.

```bash
PUBLIC_BASE_URL=https://bear.example.com python tools/make_tools.py
```

Each generated tool has:
- the headers `Content-Type: application/json`, `X-Tool-Secret` (a secret reference), and `X-Conversation-Id` (bound to `system__conversation_id`, so backend logs can be traced to a call)
- a required `shop_id` body parameter bound to the `shop_id` dynamic variable that the call-start webhook returns. It has no description, so the LLM never fills it in.
- a `response_timeout_secs` of 10

## Manual setup (ElevenLabs dashboard)
1. **Store the secret.** In Workspace settings → Secrets, add a secret whose value is the backend's `TOOL_SECRET`. For the API route below, note its id.
2. **Generate the configs** with the command above, pointed at the deployed backend.
3. **Create each tool.** Go to Agent → Tools → Add tool → Webhook, then:
   - Copy the name, description, method `POST` and URL from the JSON.
   - Headers: add `X-Tool-Secret` with type **Secret**, pointing at the secret from step 1. Add `X-Conversation-Id` with type **Dynamic variable** `system__conversation_id`.
   - Body parameters: add one per entry in `request_body_schema.properties`. Every parameter's value type is **LLM prompt** with that description, except `shop_id`, whose type is **Dynamic variable** `shop_id`. Mark the ones listed in `required`.
   - Set the response timeout to 10 seconds.
4. **Publish the agent.** Then do the call-start webhook setup in `docs/ELEVENLABS_INIT.md`, which provides `shop_id` and the other variables.

## Or create them through the API
```bash
ELEVENLABS_API_KEY=... ELEVENLABS_TOOL_SECRET_ID=sec_... python tools/create_tools_via_api.py
```
This fills in the secret reference and posts each config to `POST /v1/convai/tools` and prints the new tool ids. Add those ids to the agent and publish. It hasn't been run against the live API yet, so check the tools in the dashboard afterwards.

## Tests
`tests/test_tool_scripts.py` regenerates the configs and checks them two ways. It diffs them against `tests/fixtures/tools/` (fixed URL), and it checks that every JSON file round-trips exactly. If you change the generator, regenerate both the committed files and the fixtures:

```bash
env -u PUBLIC_BASE_URL python tools/make_tools.py
python -c "import sys; sys.path.insert(0, 'tools'); import make_tools; make_tools.main(out_dir='tests/fixtures/tools', environ={'PUBLIC_BASE_URL': 'https://bear.example.test'})"
```

## Live rehearsal
After the agent and tools exist, check that the real agent picks the right tool and arguments from caller lines in EN, ES, FR and Kreyòl:

```bash
python tools/rehearse.py --dry-run          # no key: validates tools/rehearsal_cases.yaml and prints the test payloads
ELEVENLABS_API_KEY=... ELEVENLABS_AGENT_ID=... python tools/rehearse.py --repeat 3
```

It creates one ElevenLabs agent test per case, runs each 3 times, prints a pass table, deletes the tests, and exits non-zero if any run failed. Then do the real calls in `docs/PHONE_TEST_CHECKLIST.md`.
