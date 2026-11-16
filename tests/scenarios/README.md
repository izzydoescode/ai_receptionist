# Scripted call scenarios

Each file is one call: the tool calls the ElevenLabs agent should make for one flow and language, in order. `tests/test_scenarios.py` replays them against a freshly seeded backend (the demo shop, with no holidays) and checks the results. Each scenario has to finish in under 5 seconds (the app's tool deadline is 4 seconds, so a real hang still fails). No API key or paid service is involved: these test the backend side of a call, not the live agent.

```yaml
name: es_book                 # must match the file name
language: es                  # es_* files must send and save language es on every write
description: what the caller says and why
shop: {callback_languages: [en]}   # optional overrides for the demo shop file
shops:                            # optional: more than one shop (each entry is overrides)
  - {}
  - {shop_id: other-shop, name: Other Shop}
calls:
  - tool: book_appointment    # POST /tools/<tool> with the tool secret
    repeat: 2                 # optional: send the same call N times, check the last response
    args: {...}               # the request body; "{next_monday}" ... "{next_sunday}" become dates
                              # replay injects shop_id (the default shop) and X-Conversation-Id
                              # the way the ElevenLabs tool config does, unless the step sets them
    headers: {X-Tool-Secret: "wrong-secret"}  # optional header overrides
    expect_status: 401        # optional; default 200. Non-200 responses skip the JSON checks
    expect: {booked: true}    # exact values in the response
    expect_contains: {message: bring the keys}   # substrings of response fields
    expect_length: {alternatives: 3}
    expect_missing_keys: [vehicle]               # fields that must not appear
    expect_not_in_response: [Carlos]             # text that must not appear anywhere in the response
db_shop_id: other-shop        # optional; default is the app's SHOP_ID
db_counts: {appointments: 1}  # row counts on the board for db_shop_id (or the default shop)
db_counts_by_shop:            # optional counts on other shops (to prove rows did not leak)
  vice-city-auto: {messages: 0}
db:                           # each listed row must match some saved row (subset match)
  appointments:
    - {customer_name: José Martínez, language: es}
audit:                        # each must match some audit event
  - {event: appointment.booked}
admin_contains: [José Martínez]   # text that must appear on GET /admin
```

Run them with `python -m pytest tests/test_scenarios.py -v`. To add a flow, add a file and list its name in `PLAN_SCENARIOS` if it's one of the required ones.

What these don't cover: whether the live agent actually picks these tools and arguments from real speech. That needs an ElevenLabs key (PLAN Task 13, the live rehearsal).
