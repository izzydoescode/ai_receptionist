"""Invariants for the container image (PLAN Task 11). CI's docker job builds and runs it for real."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text("utf-8")


def instructions(dockerfile):
    """(INSTRUCTION, arguments) pairs, with line continuations joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", dockerfile)
    lines = [line.strip() for line in joined.splitlines()]
    return [tuple(line.split(None, 1)) for line in lines if line and not line.startswith("#")]


def args_of(name):
    return [rest for instruction, rest in instructions(read("Dockerfile")) if instruction.upper() == name]


def test_every_base_image_is_pinned_by_digest():
    bases = args_of("FROM")
    assert bases
    for base in bases:
        image = base.split()[0]
        assert re.fullmatch(r"python:3\.13-slim-[a-z]+@sha256:[0-9a-f]{64}", image), image


def test_runtime_stage_runs_as_a_non_root_user():
    users = args_of("USER")
    assert users, "the image must switch away from root"
    final = users[-1].split(":")[0]
    assert final not in {"root", "0"}
    # USER must come after the last FROM, so it applies to the runtime stage.
    order = [instruction.upper() for instruction, _ in instructions(read("Dockerfile"))]
    assert max(i for i, name in enumerate(order) if name == "USER") > max(
        i for i, name in enumerate(order) if name == "FROM"
    )


def test_healthcheck_probes_health_endpoint():
    (check,) = args_of("HEALTHCHECK")
    assert "/health" in check
    assert "--interval" in check and "--timeout" in check


def test_uvicorn_serves_the_factory_behind_the_proxy():
    (command,) = args_of("CMD")
    for part in ['"uvicorn"', '"--factory"', '"main:create_app"', '"--proxy-headers"', '"0.0.0.0"', '"8080"']:
        assert part in command


def test_migrations_run_on_boot_only_when_asked():
    script = read("docker-entrypoint.sh")
    assert script.startswith("#!/bin/sh\nset -eu\n")
    assert 'if [ "${BEAR_MIGRATE_ON_BOOT:-0}" = "1" ]; then' in script
    assert "alembic upgrade head" in script
    assert script.rstrip().endswith('exec "$@"')
    assert (ROOT / "docker-entrypoint.sh").stat().st_mode & 0o111


def test_build_context_leaves_out_secrets_and_local_data():
    ignored = set(read(".dockerignore").split())
    for pattern in [".env", ".env.*", "**/*.db", ".git", ".venv", "tests", "**/__pycache__"]:
        assert pattern in ignored, pattern


def fly():
    import tomllib

    return tomllib.loads(read("fly.toml"))


SECRET_NAMES = ["TOOL_SECRET", "INIT_WEBHOOK_SECRET", "ADMIN_PASSWORD", "DATABASE_URL"]


def test_fly_app_is_always_on_in_the_east():
    config = fly()
    assert config["primary_region"] in {"iad", "ewr"}
    service = config["http_service"]
    # The init webhook fires while the phone rings, so it can't wait for a cold start.
    assert service["auto_stop_machines"] == "off"
    assert service["min_machines_running"] >= 1
    assert service["force_https"] is True
    assert config["vm"][0]["size"] == "shared-cpu-1x"


def test_fly_routes_to_the_image_port_and_checks_health():
    service = fly()["http_service"]
    assert service["internal_port"] == 8080
    (check,) = service["checks"]
    assert check["path"] == "/health"
    assert check["method"] == "GET"


def test_fly_migrates_in_a_release_command_not_on_boot():
    config = fly()
    assert config["deploy"]["release_command"] == "alembic upgrade head"
    assert config["env"]["BEAR_MIGRATE_ON_BOOT"] == "0"


def test_fly_env_holds_no_secrets():
    env = fly()["env"]
    for name in SECRET_NAMES:
        assert name not in env
    assert all("secret" not in key.lower() and "password" not in key.lower() for key in env)
