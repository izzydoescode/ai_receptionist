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
    "book_appointment": ["customer_name", "phone", "vehicle", "service", "preferred_date", "preferred_time"],
    "check_repair_status": ["last_name"],
    "request_tow": ["customer_name", "phone", "vehicle", "pickup_location", "issue", "is_safe"],
    "take_message": ["caller_name", "phone", "reason"],
}


@pytest.fixture
def generated_tools(tmp_path):
    # Write into tmp_path, never into the real tools/ directory.
    load_script("make_tools").main(["https://example.test/"], out_dir=tmp_path)
    return tmp_path


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


def test_generator_defaults_to_placeholder_base_url(tmp_path):
    load_script("make_tools").main([], out_dir=tmp_path)
    config = json.loads((tmp_path / "take_message.json").read_text())
    assert config["api_schema"]["url"] == "https://YOUR-NGROK-ID.ngrok-free.app/tools/take_message"


def test_committed_tool_json_matches_generator_output(tmp_path):
    make_tools = load_script("make_tools")
    make_tools.main([], out_dir=tmp_path)
    for name in REQUIRED:
        committed = json.loads((make_tools.TOOLS_DIR / f"{name}.json").read_text())
        assert committed == json.loads((tmp_path / f"{name}.json").read_text())


def test_api_creator_requires_api_key(generated_tools, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        load_script("create_tools_via_api").main(tools_dir=generated_tools)


def test_api_creator_posts_each_generated_config_with_dummy_key(generated_tools, monkeypatch):
    dummy_key = "dummy-elevenlabs-key-for-tests"
    monkeypatch.setenv("ELEVENLABS_API_KEY", dummy_key)
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        return io.BytesIO(b'{"id": "tool_x"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ids = load_script("create_tools_via_api").main(tools_dir=generated_tools)
    assert ids == ["tool_x"] * 4
    assert len(requests) == 4
    configs = {json.loads(request.data)["tool_config"]["name"]: request for request in requests}
    assert set(configs) == set(REQUIRED)
    for name, request in configs.items():
        assert request.full_url == "https://api.elevenlabs.io/v1/convai/tools"
        assert request.get_method() == "POST"
        assert {key.lower(): value for key, value in request.header_items()}["xi-api-key"] == dummy_key
        assert json.loads(request.data) == {"tool_config": json.loads((generated_tools / f"{name}.json").read_text())}
