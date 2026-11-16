"""tools/rehearse.py: the live agent rehearsal (PLAN Task 13), tested without a key or network."""

import json

import pytest
import yaml

import rehearse


def case(**overrides):
    base = {
        "name": "en_status",
        "language": "en",
        "tool": "check_repair_status",
        "chat_history": [{"role": "user", "message": "Is my car ready? Rivera, RO 1001."}],
        "params": {"ro_number": {"exact": "1001"}, "last_name": {"regex": "(?i)rivera"}},
    }
    base.update(overrides)
    return base


def write_cases(tmp_path, *cases):
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(list(cases), allow_unicode=True), "utf-8")
    return path


def test_committed_cases_are_valid_and_cover_every_go_live_flow_and_language():
    cases = rehearse.load_cases()
    covered = {(c["tool"], c["language"]) for c in cases}
    for language in ("en", "es", "fr"):
        assert ("book_appointment", language) in covered
        assert ("check_repair_status", language) in covered
    assert ("take_message", "en") in covered
    assert ("take_message", "ht") in covered


@pytest.mark.parametrize(
    "bad,message",
    [
        (case(tool="send_fax"), "unknown tool"),
        (case(chat_history=[{"role": "agent", "message": "Hi"}]), "end with the caller"),
        (case(chat_history=[{"role": "robot", "message": "Hi"}]), "role user/agent"),
        (case(params={"ro_number": {"fuzzy": "1001"}}), "exactly one of"),
        (case(params={"ro_number": {"exact": "1", "regex": "1"}}), "exactly one of"),
    ],
)
def test_invalid_cases_are_rejected(tmp_path, bad, message):
    with pytest.raises(SystemExit, match=message):
        rehearse.load_cases(write_cases(tmp_path, bad))


def test_duplicate_and_unknown_case_names(tmp_path):
    with pytest.raises(SystemExit, match="duplicate"):
        rehearse.load_cases(write_cases(tmp_path, case(), case()))
    with pytest.raises(SystemExit, match="No such case"):
        rehearse.load_cases(write_cases(tmp_path, case()), only=["nope"])


def test_payload_is_a_tool_test_with_the_shop_variables():
    variables = rehearse.demo_dynamic_variables()
    assert variables["shop_id"] == "vice-city-auto"
    payload = rehearse.test_payload(case(), "tool_123", variables)
    assert payload["name"] == "bear-rehearsal: en_status"
    assert payload["type"] == "tool"
    assert payload["dynamic_variables"] == variables
    assert payload["chat_history"] == [
        {"role": "user", "message": "Is my car ready? Rivera, RO 1001.", "time_in_call_secs": 0}
    ]
    assert payload["tool_call_parameters"] == {
        "referenced_tool": {"id": "tool_123", "type": "webhook"},
        "parameters": [
            {"path": "ro_number", "eval": {"type": "exact", "expected_value": "1001"}},
            {"path": "last_name", "eval": {"type": "regex", "pattern": "(?i)rivera"}},
        ],
        "verify_absence": False,
    }


def test_dry_run_needs_no_key_and_sends_nothing():
    lines = []
    assert rehearse.main(["--dry-run"], environ={}, out=lines.append) == 0
    assert lines[-1].startswith("dry run: 8 cases valid, 3 runs each")
    assert json.loads(lines[0])["type"] == "tool"


def test_live_run_requires_key_and_agent():
    with pytest.raises(SystemExit, match="ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID"):
        rehearse.main([], environ={"ELEVENLABS_API_KEY": "dummy"})


class FakeElevenLabs:
    def __init__(self, statuses, tools=("check_repair_status", "book_appointment", "take_message")):
        self.statuses = statuses  # case name -> list of final statuses, one per repeat
        self.tools = tools
        self.calls = []
        self.created = {}
        self.polls = 0

    def tool_ids_by_name(self):
        return {name: f"id_{name}" for name in self.tools}

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/v1/convai/agent-testing/create":
            test_id = f"test_{len(self.created)}"
            self.created[test_id] = body["name"].removeprefix(rehearse.TEST_NAME_PREFIX)
            return {"id": test_id}
        if path.endswith("/run-tests"):
            assert body["repeat_count"] == 3
            return {"id": "inv_1", "test_runs": self.runs("pending")}
        if path == "/v1/convai/test-invocations/inv_1":
            self.polls += 1
            return {"test_runs": self.runs(None)}
        if method == "DELETE":
            return {}
        raise AssertionError(path)

    def runs(self, forced_status):
        runs = []
        for test_id, name in self.created.items():
            for status in self.statuses[name]:
                runs.append(
                    {
                        "test_id": test_id,
                        "status": forced_status or status,
                        "condition_result": {"rationale": {"summary": f"{name} said: wrong tool"}},
                    }
                )
        return runs


def test_live_run_creates_runs_polls_reports_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(rehearse, "CASES_FILE", write_cases(tmp_path, case(), case(name="es_status", language="es")))
    fake = FakeElevenLabs({"en_status": ["passed"] * 3, "es_status": ["passed", "failed", "passed"]})
    lines = []
    code = rehearse.main(
        [],
        environ={"ELEVENLABS_API_KEY": "dummy", "ELEVENLABS_AGENT_ID": "agent_1"},
        client=fake,
        sleep=lambda seconds: None,
        out=lines.append,
    )
    assert code == 1  # one es_status run failed
    assert fake.polls == 1
    assert ("POST", "/v1/convai/agents/agent_1/run-tests") in [(m, p) for m, p, _ in fake.calls]
    deleted = sorted(p for m, p, _ in fake.calls if m == "DELETE")
    assert deleted == ["/v1/convai/agent-testing/test_0", "/v1/convai/agent-testing/test_1"]
    table = "\n".join(lines)
    assert "en_status        3/3" in table
    assert "es_status        2/3" in table
    assert "es_status said: wrong tool" in table


def test_all_passing_run_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(rehearse, "CASES_FILE", write_cases(tmp_path, case()))
    fake = FakeElevenLabs({"en_status": ["passed"] * 3})
    env = {"ELEVENLABS_API_KEY": "dummy", "ELEVENLABS_AGENT_ID": "agent_1"}
    assert rehearse.main([], environ=env, client=fake, sleep=lambda s: None, out=lambda line: None) == 0


def test_missing_workspace_tools_stop_before_creating_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(rehearse, "CASES_FILE", write_cases(tmp_path, case()))
    fake = FakeElevenLabs({"en_status": ["passed"]}, tools=("book_appointment",))
    env = {"ELEVENLABS_API_KEY": "dummy", "ELEVENLABS_AGENT_ID": "agent_1"}
    with pytest.raises(SystemExit, match="check_repair_status"):
        rehearse.main([], environ=env, client=fake, sleep=lambda s: None)
    assert fake.calls == []


def test_tests_are_deleted_even_when_the_run_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(rehearse, "CASES_FILE", write_cases(tmp_path, case()))
    fake = FakeElevenLabs({"en_status": ["passed"]})
    original = fake.request

    def broken_run(method, path, body=None):
        if path.endswith("/run-tests"):
            raise SystemExit("ElevenLabs POST run-tests returned HTTP 500")
        return original(method, path, body)

    fake.request = broken_run
    env = {"ELEVENLABS_API_KEY": "dummy", "ELEVENLABS_AGENT_ID": "agent_1"}
    with pytest.raises(SystemExit, match="HTTP 500"):
        rehearse.main([], environ=env, client=fake, sleep=lambda s: None)
    assert [p for m, p, _ in fake.calls if m == "DELETE"] == ["/v1/convai/agent-testing/test_0"]


def test_http_errors_show_the_detail_without_the_key():
    import io
    import urllib.error

    def failing(request, timeout):
        assert request.headers["Xi-api-key"] == "secret-key"
        raise urllib.error.HTTPError(request.full_url, 422, "bad", {}, io.BytesIO(b'{"detail": "path is invalid"}'))

    client = rehearse.ElevenLabs("secret-key", opener=failing)
    with pytest.raises(SystemExit) as error:
        client.request("POST", "/v1/convai/agent-testing/create", {"name": "x"})
    assert "HTTP 422: path is invalid" in str(error.value)
    assert "secret-key" not in str(error.value)
