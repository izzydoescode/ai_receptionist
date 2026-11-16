import pytest

from bear.shops import ShopConfigError


def test_app_loads_shops_at_startup(make_client):
    client = make_client(_shops=[{}, {"shop_id": "second-shop", "name": "Second Demo Shop"}])
    shops = client.app.state.shops
    assert sorted(shops) == ["second-shop", "vice-city-auto"]
    assert shops["second-shop"].name == "Second Demo Shop"


def test_default_shops_dir_is_the_repo_example(make_client):
    client = make_client(SHOPS_DIR=None)
    assert list(client.app.state.shops) == ["vice-city-auto"]


def test_invalid_shop_file_stops_startup_with_readable_error(make_client):
    with pytest.raises(ShopConfigError) as error:
        make_client(_seed=False, _shops=[{"transfer_number": "555-0142"}])
    assert "vice-city-auto.yaml: transfer_number: must be an E.164 number" in str(error.value)


def test_duplicate_twilio_number_stops_startup(make_client):
    shops = [{"twilio_number": "+13055550100"}, {"shop_id": "copy-shop", "twilio_number": "+13055550100"}]
    with pytest.raises(ShopConfigError, match="used by both"):
        make_client(_seed=False, _shops=shops)


def test_missing_shops_dir_stops_startup(make_client, tmp_path):
    with pytest.raises(ShopConfigError, match="SHOPS_DIR"):
        make_client(_seed=False, SHOPS_DIR=tmp_path / "nowhere")


def test_shop_id_must_have_a_shop_file(make_client):
    with pytest.raises(RuntimeError, match="SHOP_ID unknown-shop has no shop file"):
        make_client(_seed=False, SHOP_ID="unknown-shop")
