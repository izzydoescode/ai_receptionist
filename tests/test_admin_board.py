import html
import re
from datetime import UTC, datetime, timedelta

import pytest

from conftest import ADMIN_AUTH
from test_store import APPOINTMENT, MESSAGE, REPAIR, SLOT, TOW

FIXED = datetime(2026, 11, 16, 15, tzinfo=UTC)


def rendered_tables(body):
    return [
        re.findall(r"<tr>\s*(<td>.*?)</tr>", table, re.S)
        for table in re.findall(r"<table\b[^>]*>(.*?)</table>", body, re.S)
    ]


def cells(*values):
    return "".join(f"<td>{html.escape(str(value), quote=True)}</td>" for value in values)


def test_exact_board_rows(make_client):
    client = make_client(_seed=False)
    store = client.app.state.store
    shop = client.app.state.settings.shop_id
    appointment = store.book_appointment(shop, SLOT, 2, **APPOINTMENT, created_at=FIXED)
    tow = store.create_tow(shop, **TOW, created_at=FIXED)
    message = store.create_message(shop, **MESSAGE, created_at=FIXED)
    store.upsert_repair_order(shop, **REPAIR)
    response = client.get("/admin", auth=ADMIN_AUTH)
    assert response.status_code == 200
    assert rendered_tables(response.text) == [
        [
            cells(
                appointment["public_id"],
                "2026-11-16 10:00 EST",
                "Zelda Quixote",
                "+13055550199",
                "Demo sedan",
                "Private service",
                "en",
            )
        ],
        [
            cells(
                tow["public_id"],
                "2026-11-16 10:00 EST",
                "Zelda Quixote",
                "+13055550199",
                "Private street",
                "Private issue",
                True,
            )
        ],
        [
            cells(
                message["public_id"],
                "2026-11-16 10:00 EST",
                "Zelda Quixote",
                "+13055550199",
                "Private callback",
                "normal",
                "en",
            )
        ],
        [cells("9001", "Zelda Quixote", "Demo sedan", "ready", "Tomorrow")],
    ]


def test_board_newest_fifty(make_client):
    client = make_client(_seed=False)
    store = client.app.state.store
    shop = client.app.state.settings.shop_id
    records = [
        store.create_message(shop, **{**MESSAGE, "reason": f"message-{i:02}"}, created_at=FIXED + timedelta(minutes=i))
        for i in range(55)
    ]
    tables = rendered_tables(client.get("/admin", auth=ADMIN_AUTH).text)
    assert len(tables[2]) == 50
    assert [re.search(r"<td>(M[0-9A-F]{6})</td>", row)[1] for row in tables[2]] == [
        r["public_id"] for r in records[:4:-1]
    ]


def test_board_query_isolates_shops(make_client):
    client = make_client(_seed=False, SHOP_ID="shop-a", _shops=[{"shop_id": "shop-a"}, {"shop_id": "shop-b"}])
    store = client.app.state.store
    for shop in ("shop-a", "shop-b"):
        store.create_message(shop, **{**MESSAGE, "caller_name": shop})
        store.create_tow(shop, **{**TOW, "customer_name": shop})
        store.book_appointment(shop, SLOT, 2, **{**APPOINTMENT, "customer_name": shop})
        store.upsert_repair_order(shop, **{**REPAIR, "customer_name": shop})
    for shop, other in (("shop-a", "shop-b"), ("shop-b", "shop-a")):
        body = client.get(f"/admin?shop={shop}", auth=ADMIN_AUTH).text
        assert all(shop in table[0] and other not in table[0] for table in rendered_tables(body))
    assert rendered_tables(client.get("/admin", auth=ADMIN_AUTH).text) == rendered_tables(
        client.get("/admin?shop=shop-a", auth=ADMIN_AUTH).text
    )


@pytest.mark.parametrize("shop", ["", "Bad", "../shop", "a" * 65, "a\n"])
def test_invalid_board_shop(client, shop):
    response = client.get("/admin", params={"shop": shop}, auth=ADMIN_AUTH)
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid shop"}


def test_board_uses_the_requested_shops_name_and_timezone(make_client):
    west = {"shop_id": "west-shop", "name": "West Demo Shop", "timezone": "America/Los_Angeles"}
    client = make_client(_seed=False, _shops=[{}, west])
    client.app.state.store.create_message("west-shop", **MESSAGE, created_at=FIXED)
    body = client.get("/admin?shop=west-shop", auth=ADMIN_AUTH).text
    assert "<title>West Demo Shop board</title>" in body
    assert "<td>2026-11-16 07:00 PST</td>" in body
    assert "Vice City Auto" not in body


def test_unknown_board_shop_is_404_after_auth(client):
    assert client.get("/admin?shop=no-such-shop").status_code == 401
    response = client.get("/admin?shop=no-such-shop", auth=ADMIN_AUTH)
    assert response.status_code == 404
    assert response.json() == {"detail": "unknown shop"}


def test_board_query_still_authenticated_and_escaped(make_client):
    client = make_client(_shops=[{}, {"shop_id": "other"}])
    name = "<script>alert('dummy')</script>"
    client.app.state.store.create_message("other", **{**MESSAGE, "caller_name": name})
    assert client.get("/admin?shop=other").status_code == 401
    body = client.get("/admin?shop=other", auth=ADMIN_AUTH).text
    assert html.escape(name, quote=True) in body
    assert name not in body


def test_unknown_number_messages_have_their_own_board(make_client):
    client = make_client(_seed=False)
    client.app.state.store.create_message("unknown", **{**MESSAGE, "caller_name": "Unmatched Caller"}, created_at=FIXED)
    body = client.get("/admin?shop=unknown", auth=ADMIN_AUTH).text
    assert "<title>Unrecognized numbers board</title>" in body
    assert "Unmatched Caller" in body
    assert "<td>2026-11-16 10:00 EST</td>" in body
    assert "Unmatched Caller" not in client.get("/admin", auth=ADMIN_AUTH).text
