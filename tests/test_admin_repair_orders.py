"""Staff create, edit and close repair orders under /admin/ros; callers then hear the ETA."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bear.admin import CSRF_MAX_AGE_SECONDS, CsrfSigner
from conftest import ADMIN_AUTH

MIAMI = ZoneInfo("America/New_York")
FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


def csrf_token(client, path="/admin/ros/new"):
    page = client.get(path, auth=ADMIN_AUTH)
    assert page.status_code == 200, page.text
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def order_form(client, **overrides):
    tomorrow = (datetime.now(MIAMI) + timedelta(days=1)).date().isoformat()
    form = {
        "csrf_token": csrf_token(client),
        "mode": "create",
        "ro_number": "2001",
        "customer_name": "Demo Customer",
        "phone": "(305) 555-0177",
        "vehicle": "2020 Demo Hatchback",
        "plate": "DEMO77",
        "status": "in_progress",
        "eta_date": tomorrow,
        "eta_time": "16:30",
        "eta_note": "waiting on the alignment",
        "note": "Front brakes and alignment.",
        "balance_estimate": "$350",
    }
    form.update(overrides)
    return form


def post_form(client, form, auth=ADMIN_AUTH, headers=FORM_HEADERS, shop="vice-city-auto"):
    return client.post(f"/admin/ros?shop={shop}", data=form, auth=auth, headers=headers, follow_redirects=False)


def status_lookup(client, tool_headers, **payload):
    response = client.post(
        "/tools/check_repair_status",
        json={"shop_id": "vice-city-auto", **payload},
        headers=tool_headers,
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("path", ["/admin/ros", "/admin/ros/new", "/admin/ros/edit?ro=1001"])
def test_pages_require_admin_login(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, auth=("admin", "wrong")).status_code == 401
    assert client.get(path, auth=ADMIN_AUTH).status_code == 200


def test_post_requires_admin_login(client):
    assert post_form(client, order_form(client), auth=None).status_code == 401


def test_list_shows_seeded_orders_with_spoken_eta(client):
    page = client.get("/admin/ros", auth=ADMIN_AUTH)
    assert "1001" in page.text and "Carlos Rivera" in page.text
    assert "tomorrow by 3 PM" in page.text
    assert page.headers["cache-control"] == "no-store"


def test_board_links_to_repair_orders(client):
    assert 'href="/admin/ros?shop=vice-city-auto"' in client.get("/admin", auth=ADMIN_AUTH).text


def test_create_then_caller_hears_the_eta(client, tool_headers):
    response = post_form(client, order_form(client))
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/ros?shop=vice-city-auto&saved=2001"
    assert "Saved repair order 2001" in client.get(response.headers["location"], auth=ADMIN_AUTH).text

    result = status_lookup(client, tool_headers, last_name="Customer", ro_number="2001")
    assert result["found"] is True
    assert result["status"] == "being worked on right now"
    assert result["eta"] == "tomorrow by 4:30 PM (waiting on the alignment)"
    assert result["details"] == "Front brakes and alignment."


def test_create_stores_normalized_phone_and_audits(client):
    post_form(client, order_form(client))
    store = client.app.state.store
    order = store.get_repair_order("vice-city-auto", "2001")
    assert order["phone"] == "+13055550177"
    assert order["eta_at"].astimezone(MIAMI).strftime("%H:%M") == "16:30"
    board = store.board("vice-city-auto")
    assert board  # the store still answers after the write
    from sqlalchemy import select

    from bear.db import audit_events

    with store.engine.connect() as conn:
        events = conn.execute(
            select(audit_events.c.event).where(audit_events.c.entity_public_id == order["public_id"])
        ).scalars()
        assert list(events) == ["repair_order.created"]


def test_edit_updates_and_close_hides_from_callers(client, tool_headers):
    post_form(client, order_form(client))
    edit_page = client.get("/admin/ros/edit?ro=2001", auth=ADMIN_AUTH)
    assert 'value="16:30"' in edit_page.text
    assert "readonly" in edit_page.text
    token = re.search(r'name="csrf_token" value="([^"]+)"', edit_page.text).group(1)

    updated = order_form(client, csrf_token=token, mode="edit", status="ready_for_pickup", eta_date="", eta_time="")
    assert post_form(client, updated).status_code == 303
    result = status_lookup(client, tool_headers, last_name="Customer", ro_number="2001")
    assert result["status"] == "ready for pickup"
    assert result["eta"] == "waiting on the alignment"

    closed = order_form(client, mode="edit", status="picked_up")
    assert post_form(client, closed).status_code == 303
    assert status_lookup(client, tool_headers, last_name="Customer", ro_number="2001")["found"] is False
    listing = client.get("/admin/ros", auth=ADMIN_AUTH).text
    assert listing.index("2001") > listing.index("Picked up")


def test_create_refuses_an_existing_ro_number(client):
    response = post_form(client, order_form(client, ro_number="1001"))
    assert response.status_code == 400
    assert "already exists" in response.text
    assert client.app.state.store.get_repair_order("vice-city-auto", "1001")["customer_name"] == "Carlos Rivera"


def test_edit_of_a_missing_ro_is_refused(client):
    response = post_form(client, order_form(client, mode="edit", ro_number="9999"))
    assert response.status_code == 400
    assert "exist anymore" in response.text


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("phone", "555-0101", "10-digit US number"),
        ("ro_number", "10 01", "letters, digits or dashes"),
        ("customer_name", "", "Customer name"),
        ("status", "teleported", "pick one from the list"),
        ("eta_time", "", "both the date and the time"),
        ("eta_date", "2026-02-30", "Pick a date"),
    ],
)
def test_bad_input_redisplays_the_form_with_a_hint(client, field, value, message):
    response = post_form(client, order_form(client, **{field: value}))
    assert response.status_code == 400
    assert message in response.text
    assert client.app.state.store.get_repair_order("vice-city-auto", "2001") is None


def test_every_value_is_escaped(client):
    script = "<script>alert(1)</script>"
    post_form(client, order_form(client, customer_name=f"Demo {script}", eta_note=script))
    for page in (client.get("/admin/ros", auth=ADMIN_AUTH), client.get("/admin/ros/edit?ro=2001", auth=ADMIN_AUTH)):
        assert script not in page.text
        assert "&lt;script&gt;" in page.text
    bad = post_form(client, order_form(client, ro_number=script))
    assert script not in bad.text


@pytest.mark.parametrize("token", ["", "garbage", "123.abc"])
def test_post_without_a_valid_csrf_token_is_refused(client, token):
    assert post_form(client, order_form(client, csrf_token=token)).status_code == 403


def test_csrf_token_is_bound_to_the_shop(make_client):
    client = make_client(_shops=({}, {"shop_id": "west-shop", "name": "West Shop"}))
    form = order_form(client)  # token issued for vice-city-auto
    assert post_form(client, form, shop="west-shop").status_code == 403


def test_cross_site_origin_is_refused(client):
    headers = {**FORM_HEADERS, "Origin": "https://evil.example"}
    assert post_form(client, order_form(client), headers=headers).status_code == 403


def test_same_site_origin_is_allowed(client):
    headers = {**FORM_HEADERS, "Origin": "http://testserver"}
    assert post_form(client, order_form(client), headers=headers).status_code == 303


def test_json_posts_are_refused(client):
    response = client.post("/admin/ros", json=order_form(client), auth=ADMIN_AUTH)
    assert response.status_code == 415


def test_oversized_form_is_refused(client):
    assert post_form(client, order_form(client, note="x" * 20000)).status_code == 413


def test_unknown_shop_and_ro_are_404(client):
    assert client.get("/admin/ros?shop=nope", auth=ADMIN_AUTH).status_code == 404
    assert client.get("/admin/ros/edit?ro=9999", auth=ADMIN_AUTH).status_code == 404


def test_csrf_signer_expires_and_rejects_tampering():
    signer = CsrfSigner(b"k" * 32)
    token = signer.issue("vice-city-auto", now=1_000_000)
    assert signer.verify(token, "vice-city-auto", now=1_000_000 + CSRF_MAX_AGE_SECONDS)
    assert not signer.verify(token, "vice-city-auto", now=1_000_001 + CSRF_MAX_AGE_SECONDS)
    assert not signer.verify(token, "vice-city-auto", now=999_999)
    assert not signer.verify(token, "west-shop", now=1_000_000)
    assert not signer.verify(token.replace(".", ".0", 1), "vice-city-auto", now=1_000_000)
    assert not CsrfSigner(b"x" * 32).verify(token, "vice-city-auto", now=1_000_000)
    assert CsrfSigner().key != CsrfSigner().key  # a fresh random key per app start
