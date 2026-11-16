"""Live rehearsal: check that the real ElevenLabs agent calls the right tool with the right arguments.

PLAN Task 13. For each case in tools/rehearsal_cases.yaml this creates an ElevenLabs agent test
(type "tool"), runs it against the agent several times (routing is probabilistic), waits for the
results, prints a table, and deletes the tests it created.

Usage:
  python tools/rehearse.py --dry-run                  # no key, no network: validate cases, show payloads
  ELEVENLABS_API_KEY=... ELEVENLABS_AGENT_ID=... python tools/rehearse.py [--repeat 3] [--only en_book]

The agent's dynamic variables come from the demo shop's call-start webhook response, so the
prompt renders the way it does on a real call. Uses the ElevenLabs agent-testing API
(/v1/convai/agent-testing, /v1/convai/agents/{id}/run-tests, /v1/convai/test-invocations).
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = Path(__file__).resolve().parent / "rehearsal_cases.yaml"
API = "https://api.elevenlabs.io"
TEST_NAME_PREFIX = "bear-rehearsal: "
EVAL_TYPES = {"exact": "expected_value", "regex": "pattern", "llm": "description", "anything": None}
KNOWN_TOOLS = {"book_appointment", "check_repair_status", "request_tow", "take_message"}
POLL_SECONDS = 5
POLL_ATTEMPTS = 120


def load_cases(path=None, only=None):
    cases = yaml.safe_load(Path(path or CASES_FILE).read_text("utf-8"))
    problems = []
    names = set()
    for index, case in enumerate(cases):
        where = case.get("name", f"case {index}")
        if case.get("name") in names:
            problems.append(f"{where}: duplicate name")
        names.add(case.get("name"))
        if case.get("tool") not in KNOWN_TOOLS:
            problems.append(f"{where}: unknown tool {case.get('tool')!r}")
        history = case.get("chat_history") or []
        if not history or history[-1].get("role") != "user":
            problems.append(f"{where}: chat_history must end with the caller's (user) turn")
        if any(turn.get("role") not in {"user", "agent"} or not turn.get("message") for turn in history):
            problems.append(f"{where}: each turn needs role user/agent and a message")
        for param, check in (case.get("params") or {}).items():
            if not isinstance(check, dict) or len(check) != 1 or next(iter(check)) not in EVAL_TYPES:
                problems.append(f"{where}: param {param} needs exactly one of {sorted(EVAL_TYPES)}")
    if problems:
        raise SystemExit("Invalid rehearsal cases:\n  " + "\n  ".join(problems))
    if only:
        unknown = set(only) - names
        if unknown:
            raise SystemExit(f"No such case: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["name"] in only]
    return cases


def demo_dynamic_variables(now=None):
    """The dynamic variables the call-start webhook would send for the demo shop."""
    sys.path.insert(0, str(ROOT / "backend"))
    from bear.initiation import shop_initiation
    from bear.shops import load_shops

    shop = load_shops(os.getenv("SHOPS_DIR") or ROOT / "shops")[os.getenv("SHOP_ID", "vice-city-auto")]
    response = shop_initiation(shop, now or datetime.now(shop.tz))
    return response.dynamic_variables.model_dump()


def eval_spec(check):
    kind, value = next(iter(check.items()))
    field = EVAL_TYPES[kind]
    return {"type": kind} if field is None else {"type": kind, field: value}


def test_payload(case, tool_id, dynamic_variables):
    history = [
        {"role": turn["role"], "message": turn["message"], "time_in_call_secs": index * 5}
        for index, turn in enumerate(case["chat_history"])
    ]
    return {
        "name": TEST_NAME_PREFIX + case["name"],
        "type": "tool",
        "chat_history": history,
        "dynamic_variables": dynamic_variables,
        "check_any_tool_matches": True,
        "tool_call_parameters": {
            "referenced_tool": {"id": tool_id, "type": "webhook"},
            "parameters": [
                {"path": name, "eval": eval_spec(check)} for name, check in (case.get("params") or {}).items()
            ],
            "verify_absence": False,
        },
    }


class ElevenLabs:
    def __init__(self, api_key, base=API, opener=urllib.request.urlopen):
        self.api_key = api_key
        self.base = base
        self.opener = opener

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
        )
        try:
            with self.opener(req, timeout=60) as response:
                text = response.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except (ValueError, AttributeError):
                pass  # Not a JSON object: report the raw body.
            raise SystemExit(f"ElevenLabs {method} {path} returned HTTP {error.code}: {detail}") from None
        return json.loads(text)

    def tool_ids_by_name(self):
        ids, cursor = {}, None
        while True:
            page = self.request("GET", "/v1/convai/tools?page_size=100" + (f"&cursor={cursor}" if cursor else ""))
            for tool in page.get("tools", []):
                name = (tool.get("tool_config") or {}).get("name")
                if name:
                    ids[name] = tool["id"]
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                return ids


def run_rehearsal(cases, client, agent_id, repeat, dynamic_variables, sleep=time.sleep, out=print):
    tool_ids = client.tool_ids_by_name()
    missing = sorted({case["tool"] for case in cases} - set(tool_ids))
    if missing:
        raise SystemExit(f"These tools aren't in the ElevenLabs workspace yet: {', '.join(missing)} (tools/README.md)")
    created = []
    try:
        for case in cases:
            test = client.request(
                "POST", "/v1/convai/agent-testing/create", test_payload(case, tool_ids[case["tool"]], dynamic_variables)
            )
            created.append((case["name"], test["id"]))
        invocation = client.request(
            "POST",
            f"/v1/convai/agents/{agent_id}/run-tests",
            {
                "tests": [{"test_id": test_id} for _, test_id in created],
                "repeat_count": repeat,
                "name": "bear rehearsal",
            },
        )
        runs = invocation.get("test_runs", [])
        for _ in range(POLL_ATTEMPTS):
            if runs and all(run.get("status") != "pending" for run in runs):
                break
            sleep(POLL_SECONDS)
            runs = client.request("GET", f"/v1/convai/test-invocations/{invocation['id']}").get("test_runs", [])
        else:
            raise SystemExit("Timed out waiting for the test runs to finish")
    finally:
        for _, test_id in created:
            try:
                client.request("DELETE", f"/v1/convai/agent-testing/{test_id}")
            except SystemExit as error:
                out(f"warning: could not delete test {test_id}: {error}")

    names_by_id = {test_id: name for name, test_id in created}
    results = {name: [] for name, _ in created}
    for run in runs:
        name = names_by_id.get(run.get("test_id"), run.get("test_id", "?"))
        summary = ((run.get("condition_result") or {}).get("rationale") or {}).get("summary", "")
        results.setdefault(name, []).append((run.get("status"), summary))
    out(f"{'case':<14} {'passed':>7}  notes")
    failed = False
    for case in cases:
        outcomes = results.get(case["name"], [])
        passed = sum(1 for status, _ in outcomes if status == "passed")
        notes = next((summary for status, summary in outcomes if status != "passed" and summary), "")
        failed |= passed < len(outcomes) or not outcomes
        out(f"{case['name']:<14} {passed:>3}/{len(outcomes):<3}  {notes[:100]}")
    return 1 if failed else 0


def main(argv=None, environ=None, client=None, sleep=time.sleep, out=print):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true", help="validate cases and print payloads; no network")
    parser.add_argument("--repeat", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("--only", nargs="*", help="run only these case names")
    args = parser.parse_args(argv)
    cases = load_cases(only=args.only)
    variables = demo_dynamic_variables()
    if args.dry_run:
        for case in cases:
            payload = test_payload(case, f"<{case['tool']} tool id>", variables)
            out(json.dumps(payload, indent=2, ensure_ascii=False))
        out(f"dry run: {len(cases)} cases valid, {args.repeat} runs each; nothing was sent")
        return 0
    key, agent_id = environ.get("ELEVENLABS_API_KEY"), environ.get("ELEVENLABS_AGENT_ID")
    if not key or not agent_id:
        raise SystemExit("Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID (or use --dry-run).")
    return run_rehearsal(cases, client or ElevenLabs(key), agent_id, args.repeat, variables, sleep=sleep, out=out)


if __name__ == "__main__":
    raise SystemExit(main())
