import logging

import pytest

from conftest import SECRET


@pytest.mark.parametrize("secret", [None, "", "z" * 31])
@pytest.mark.parametrize("environment", [None, "production"])
def test_non_dev_startup_requires_a_long_init_secret(make_client, secret, environment):
    with pytest.raises(RuntimeError, match="INIT_WEBHOOK_SECRET"):
        make_client(_seed=False, INIT_WEBHOOK_SECRET=secret, BEAR_ENV=environment)


def test_init_secret_must_differ_from_tool_secret(make_client):
    with pytest.raises(RuntimeError, match="INIT_WEBHOOK_SECRET must differ from TOOL_SECRET"):
        make_client(_seed=False, INIT_WEBHOOK_SECRET=SECRET)


def test_dev_may_run_without_init_secret_with_a_warning(make_client, caplog):
    with caplog.at_level(logging.WARNING, logger="bear"):
        client = make_client(_seed=False, BEAR_ENV="dev", INIT_WEBHOOK_SECRET=None)
    assert client.app.state.settings.init_webhook_secret == ""
    assert "INIT_WEBHOOK_SECRET is unset" in caplog.text


def test_valid_init_secret_is_kept_in_settings(make_client):
    client = make_client(_seed=False, INIT_WEBHOOK_SECRET="i" * 40)
    assert client.app.state.settings.init_webhook_secret == "i" * 40


def test_previous_init_secret_must_differ_from_tool_secret(make_client):
    with pytest.raises(RuntimeError, match="INIT_WEBHOOK_SECRET_PREVIOUS must differ from TOOL_SECRET"):
        make_client(_seed=False, INIT_WEBHOOK_SECRET="i" * 40, INIT_WEBHOOK_SECRET_PREVIOUS=SECRET)


def test_non_dev_startup_requires_a_long_previous_init_secret_when_set(make_client):
    with pytest.raises(RuntimeError, match="INIT_WEBHOOK_SECRET_PREVIOUS"):
        make_client(
            _seed=False,
            INIT_WEBHOOK_SECRET="i" * 40,
            INIT_WEBHOOK_SECRET_PREVIOUS="z" * 31,
            BEAR_ENV="production",
        )


def test_valid_previous_init_secret_is_kept_in_settings(make_client):
    client = make_client(_seed=False, INIT_WEBHOOK_SECRET="i" * 40, INIT_WEBHOOK_SECRET_PREVIOUS="p" * 40)
    assert client.app.state.settings.init_webhook_secret_previous == "p" * 40
