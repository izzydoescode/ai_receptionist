import importlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

SECRET = "dummy-tool-secret-" + "x" * 22  # 40 characters
ADMIN_AUTH = ("admin", "dummy-admin-password")
CONFIG_KEYS = (
    "TOOL_SECRET", "BEAR_ENV", "ADMIN_USER", "ADMIN_PASSWORD",
    "DATA_FILE", "SHOP_NAME", "SHOP_TZ",
)


def future_day(weekday):
    today = datetime.now(ZoneInfo("America/New_York")).date()
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    clients = []

    def make(**env):
        for key in CONFIG_KEYS:
            monkeypatch.delenv(key, raising=False)
        settings = {
            "TOOL_SECRET": SECRET,
            "ADMIN_PASSWORD": ADMIN_AUTH[1],
            "DATA_FILE": str(tmp_path / f"data-{len(clients)}.json"),
        }
        settings.update(env)
        for key, value in settings.items():
            if value is not None:
                monkeypatch.setenv(key, str(value))
        main = importlib.import_module("main")
        client = TestClient(main.create_app())
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def tool_headers():
    return {"X-Tool-Secret": SECRET}


@pytest.fixture
def payloads():
    return {
        "book_appointment": {
            "customer_name": "Demo Driver", "phone": "3055550199",
            "vehicle": "2017 Demo Sedan", "service": "oil change",
            "preferred_date": future_day(0).isoformat(), "preferred_time": "10:00",
        },
        "check_repair_status": {"ro_number": "1001"},
        "request_tow": {
            "customer_name": "Demo Driver", "phone": "3055550199",
            "vehicle": "2017 Demo Sedan", "pickup_location": "Demo intersection",
            "issue": "flat tire", "is_safe": True,
        },
        "take_message": {
            "caller_name": "Demo Driver", "phone": "3055550199", "reason": "Please call",
        },
    }
