import importlib
import io
import json
import urllib.request
from pathlib import Path

import pytest

TOOLS_SRC = Path(__file__).resolve().parents[1] / "tools"


def load_script(name):
    # Import lazily, and only once the script is import-safe (has a main()),
    # so a script with top-level side effects never writes into tools/ or
    # needs a real API key during collection.
    source = (TOOLS_SRC / f"{name}.py").read_text()
    if "def main(" not in source:
        pytest.fail(f"tools/{name}.py has no main(); it is not import-safe yet")
    return importlib.import_module(name)


REQUIRED = {
    "book_appointment": ["customer_name", "phone", "vehicle", "service", "preferred_date", "preferred_time", "shop_id"],
    "check_repair_status": ["last_name", "shop_id"],
    "request_tow": ["customer_name", "phone", "vehicle", "pickup_location", "issue", "is_safe", "shop_id"],
    "take_message": ["caller_name", "phone", "reason", "shop_id"],
}


@pytest.fixture
def generated_tools(tmp_path):
    # Write into tmp_path, never into the real tools/ directory.
    environ = {"PUBLIC_BASE_URL": "https://example.test/"}
    load_script("make_tools").main(out_dir=tmp_path, environ=environ)
    return tmp_path


def test_tool_schemas_do_not_expose_shop_id_to_the_llm(generated_tools):
    make_tools = load_script("make_tools")
    for name, (_description, properties, required) in make_tools.TOOLS.items():
        assert "shop_id" not in properties
        assert "shop_id" not in required
        for path in (generated_tools / f"{name}.json", make_tools.TOOLS_DIR / f"{name}.json"):
            schema = json.loads(path.read_text())["api_schema"]["request_body_schema"]
            shop_id = schema["properties"]["shop_id"]
            assert shop_id == {"type": "string", "dynamic_variable": "shop_id"}
            assert "description" not in shop_id
            assert "shop_id" in schema.get("required", [])
            conversation_id = schema["properties"].get("conversation_id")
            if name == "take_message":
                assert conversation_id == {"type": "string", "dynamic_variable": "conversation_id"}
                assert "description" not in conversation_id
                assert "conversation_id" not in schema.get("required", [])
            else:
                assert conversation_id is None


def test_generator_writes_four_post_schemas_with_required_fields(generated_tools):
    files = list(generated_tools.glob("*.json"))
    assert {path.stem for path in files} == set(REQUIRED)
    for path in files:
        config = json.loads(path.read_text())
        assert config["name"] == path.stem
        schema = config["api_schema"]
        assert schema["url"] == f"https://example.test/tools/{path.stem}"
        assert schema["method"] == "POST"
        assert schema["request_body_schema"]["required"] == REQUIRED[path.stem]


def test_shop_id_is_bound_to_the_dynamic_variable_not_the_llm(generated_tools):
    for path in generated_tools.glob("*.json"):
        properties = json.loads(path.read_text())["api_schema"]["request_body_schema"]["properties"]
        # ElevenLabs fills a property with dynamic_variable from the conversation; it has no
        # description, so the LLM is never asked to supply it.
        assert properties["shop_id"] == {"type": "string", "dynamic_variable": "shop_id"}
        dynamic = {"shop_id", "conversation_id"} if path.stem == "take_message" else {"shop_id"}
        assert all("dynamic_variable" not in spec for name, spec in properties.items() if name not in dynamic)


def test_generator_defaults_to_placeholder_base_url(tmp_path):
    load_script("make_tools").main(out_dir=tmp_path, environ={})
    config = json.loads((tmp_path / "take_message.json").read_text())
    assert config["api_schema"]["url"] == "https://YOUR-BACKEND-HOST/tools/take_message"


def test_generator_reads_public_base_url_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bear.example.test")
    load_script("make_tools").main(out_dir=tmp_path)
    config = json.loads((tmp_path / "book_appointment.json").read_text())
    assert config["api_schema"]["url"] == "https://bear.example.test/tools/book_appointment"


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:8000/"])
def test_plain_http_is_allowed_only_for_local_testing(tmp_path, base):
    load_script("make_tools").main(out_dir=tmp_path, environ={"PUBLIC_BASE_URL": base})
    url = json.loads((tmp_path / "take_message.json").read_text())["api_schema"]["url"]
    assert url == base.rstrip("/") + "/tools/take_message"


@pytest.mark.parametrize(
    "base",
    [
        "http://bear.example.test",
        "ftp://bear.example.test",
        "bear.example.test",
        "https://",
        "https://bear.example.test/api",
        "https://bear.example.test?x=1",
        "https://user:pw@bear.example.test",
    ],
)
def test_bad_public_base_url_is_rejected_without_writing(tmp_path, base):
    with pytest.raises(SystemExit, match="PUBLIC_BASE_URL"):
        load_script("make_tools").main(out_dir=tmp_path, environ={"PUBLIC_BASE_URL": base})
    assert list(tmp_path.iterdir()) == []


def test_committed_tool_json_matches_generator_output(tmp_path):
    make_tools = load_script("make_tools")
    make_tools.main(out_dir=tmp_path, environ={})
    for name in REQUIRED:
        committed = json.loads((make_tools.TOOLS_DIR / f"{name}.json").read_text())
        assert committed == json.loads((tmp_path / f"{name}.json").read_text())


DUMMY_KEY = "dummy-elevenlabs-key-for-tests"
UPLOAD_ENV = {"ELEVENLABS_API_KEY": DUMMY_KEY, "ELEVENLABS_TOOL_SECRET_ID": "sec_demo123"}


@pytest.fixture
def fake_api(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        return io.BytesIO(b'{"id": "tool_x"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


def upload(directory, environ):
    return load_script("create_tools_via_api").main(tools_dir=directory, environ=environ)


def test_api_creator_requires_api_key(generated_tools, fake_api):
    with pytest.raises(SystemExit, match="ELEVENLABS_API_KEY"):
        upload(generated_tools, {"ELEVENLABS_TOOL_SECRET_ID": "sec_demo123"})
    assert fake_api == []


def test_api_creator_posts_each_generated_config_with_dummy_key(generated_tools, fake_api):
    requests = fake_api
    ids = upload(generated_tools, UPLOAD_ENV)
    assert ids == ["tool_x"] * 4
    assert len(requests) == 4
    configs = {json.loads(request.data)["tool_config"]["name"]: request for request in requests}
    assert set(configs) == set(REQUIRED)
    for name, request in configs.items():
        assert request.full_url == "https://api.elevenlabs.io/v1/convai/tools"
        assert request.get_method() == "POST"
        assert {key.lower(): value for key, value in request.header_items()}["xi-api-key"] == DUMMY_KEY
        expected = json.loads((generated_tools / f"{name}.json").read_text())
        expected["api_schema"]["request_headers"]["X-Tool-Secret"] = {"secret_id": "sec_demo123"}
        assert json.loads(request.data) == {"tool_config": expected}


def headers_of(directory, name="take_message"):
    return json.loads((directory / f"{name}.json").read_text())["api_schema"]["request_headers"]


def test_files_on_disk_only_ever_hold_a_placeholder_secret_reference(tmp_path):
    # Even with the real values in the environment, nothing secret-related is written to disk.
    environ = {"PUBLIC_BASE_URL": "https://example.test", "TOOL_SECRET": "z" * 40, "ELEVENLABS_TOOL_SECRET_ID": "sec_x"}
    load_script("make_tools").main(out_dir=tmp_path, environ=environ)
    for path in tmp_path.glob("*.json"):
        assert headers_of(tmp_path, path.stem)["X-Tool-Secret"] == {"secret_id": "YOUR-TOOL-SECRET-ID"}
        assert "z" * 40 not in path.read_text()
        assert "sec_x" not in path.read_text()


def test_upload_leaves_the_files_on_disk_untouched(generated_tools, fake_api):
    before = {path.name: path.read_text() for path in generated_tools.glob("*.json")}
    upload(generated_tools, UPLOAD_ENV)
    assert {path.name: path.read_text() for path in generated_tools.glob("*.json")} == before


@pytest.mark.parametrize("secret_id", [None, "", "has space", "a/b", "x" * 129, '"quoted"', "z" * 40])
def test_upload_refuses_a_missing_malformed_or_secret_value_id(generated_tools, fake_api, secret_id):
    environ = {"ELEVENLABS_API_KEY": DUMMY_KEY, "TOOL_SECRET": "z" * 40}
    if secret_id is not None:
        environ["ELEVENLABS_TOOL_SECRET_ID"] = secret_id
    with pytest.raises(SystemExit, match="ELEVENLABS_TOOL_SECRET_ID"):
        upload(generated_tools, environ)
    assert fake_api == []


def test_tools_send_the_conversation_id_header_from_a_system_variable(generated_tools):
    # The backend logs X-Conversation-Id (Task 4); ElevenLabs fills it per call.
    for path in generated_tools.glob("*.json"):
        assert headers_of(generated_tools, path.stem)["X-Conversation-Id"] == {
            "variable_name": "system__conversation_id"
        }


FIXTURES = Path(__file__).resolve().parent / "fixtures/tools"
FIXTURE_ENV = {"PUBLIC_BASE_URL": "https://bear.example.test"}


def test_generated_json_matches_the_fixture_exactly(tmp_path):
    import difflib

    load_script("make_tools").main(out_dir=tmp_path, environ=FIXTURE_ENV)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(p.name for p in FIXTURES.iterdir())
    for fixture in FIXTURES.glob("*.json"):
        expected = fixture.read_text("utf-8").splitlines()
        actual = (tmp_path / fixture.name).read_text("utf-8").splitlines()
        assert actual == expected, "\n".join(
            difflib.unified_diff(expected, actual, "fixture", "generated", lineterm="")
        )


@pytest.mark.parametrize("directory", [FIXTURES, TOOLS_SRC])
def test_tool_json_round_trips(directory):
    for path in directory.glob("*.json"):
        text = path.read_text("utf-8")
        assert json.dumps(json.loads(text), indent=2, ensure_ascii=False) == text


def test_api_creator_refuses_configs_with_a_placeholder_base_url(tmp_path, fake_api):
    load_script("make_tools").main(out_dir=tmp_path, environ={})
    with pytest.raises(SystemExit, match="placeholder"):
        upload(tmp_path, UPLOAD_ENV)
    assert fake_api == []


def test_tool_descriptions_explain_ok_false():
    tools = load_script("make_tools").TOOLS
    for name, (description, _props, _required) in tools.items():
        assert "invalid_input" in description
        assert "timeout" in description
        assert "internal_error" in description
        lower = description.lower()
        assert "re-ask" in lower
        if name == "take_message":
            assert "call them back" in lower or "call back" in lower
        else:
            assert "take a message" in lower


def test_prompt_tells_agent_what_to_do_when_a_tool_fails():
    prompt = (Path(__file__).resolve().parents[1] / "agent/system_prompt.md").read_text()
    assert "invalid_input" in prompt
    assert "timeout" in prompt
    assert "internal_error" in prompt
    assert "re-ask" in prompt.lower()
