import pytest
import yaml

from bear.shops import ShopConfigError, load_shops
from test_shop_config import config


def write(directory, name, data):
    path = directory / name
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def shop_data(shop_id, number="+13055550100", **changes):
    return config(shop_id=shop_id, twilio_number=number, **changes)


def load_error(directory):
    with pytest.raises(ShopConfigError) as error:
        load_shops(directory)
    return str(error.value)


def test_loads_every_yaml_file_keyed_by_shop_id(tmp_path):
    write(tmp_path, "shop-a.yaml", shop_data("shop-a"))
    write(tmp_path, "shop-b.yaml", shop_data("shop-b", "+13055550101"))
    write(tmp_path, "notes.txt", "ignored")
    shops = load_shops(tmp_path)
    assert list(shops) == ["shop-a", "shop-b"]
    assert shops["shop-b"].twilio_number == "+13055550101"


def test_accepts_string_path(tmp_path):
    write(tmp_path, "shop-a.yaml", shop_data("shop-a"))
    assert list(load_shops(str(tmp_path))) == ["shop-a"]


def test_unquoted_yaml_dates_and_quoted_times_load(tmp_path):
    text = yaml.safe_dump(shop_data("shop-a", holidays=[])) + "holidays:\n- 2026-12-25\n"
    write(tmp_path, "shop-a.yaml", text.replace("holidays: []\n", ""))
    assert load_shops(tmp_path)["shop-a"].holidays[0].isoformat() == "2026-12-25"


def test_missing_directory(tmp_path):
    assert "SHOPS_DIR" in load_error(tmp_path / "missing")


def test_empty_directory(tmp_path):
    assert "no shop files" in load_error(tmp_path)


def test_invalid_yaml_names_file_and_line(tmp_path):
    write(tmp_path, "shop-a.yaml", "shop_id: shop-a\nname: [unclosed\n")
    text = load_error(tmp_path)
    assert "shop-a.yaml" in text
    assert "invalid YAML" in text
    assert "line" in text


@pytest.mark.parametrize("text", ["", "- just\n- a list\n", "plain string\n"])
def test_non_mapping_file(tmp_path, text):
    write(tmp_path, "shop-a.yaml", text)
    assert "shop-a.yaml: must be a mapping" in load_error(tmp_path)


def test_validation_errors_are_readable_with_paths(tmp_path):
    data = shop_data("shop-a", "305-555-0100")
    data["hours"]["monday"] = [{"open": "18:00", "close": "08:00"}]
    write(tmp_path, "shop-a.yaml", data)
    text = load_error(tmp_path)
    assert "shop-a.yaml: twilio_number: must be an E.164 number like +13055550100" in text
    assert "shop-a.yaml: hours.monday[0]: close must be after open" in text
    assert "Value error" not in text
    assert "305-555-0100" not in text  # inputs stay out of error text


def test_whole_model_errors_have_no_dangling_path(tmp_path):
    data = shop_data("shop-a")
    data["first_messages"]["en"] = "Hello, what can I do for you?"
    write(tmp_path, "shop-a.yaml", data)
    assert 'shop-a.yaml: first_messages.en must include the recording notice "may be recorded"' in load_error(tmp_path)


def test_shop_id_must_match_file_name(tmp_path):
    write(tmp_path, "shop-a.yaml", shop_data("shop-b"))
    assert "shop-a.yaml: shop_id shop-b must match the file name" in load_error(tmp_path)


def test_duplicate_twilio_number_names_both_files(tmp_path):
    write(tmp_path, "shop-a.yaml", shop_data("shop-a"))
    write(tmp_path, "shop-b.yaml", shop_data("shop-b"))
    text = load_error(tmp_path)
    assert "twilio_number +13055550100 is used by both shop-a.yaml and shop-b.yaml" in text


def test_reports_every_bad_file_at_once(tmp_path):
    write(tmp_path, "shop-a.yaml", shop_data("shop-a", name=""))
    write(tmp_path, "shop-b.yaml", shop_data("shop-b", "+13055550101", bays_per_slot=0))
    text = load_error(tmp_path)
    assert "shop-a.yaml: name:" in text
    assert "shop-b.yaml: bays_per_slot:" in text
    assert text.startswith("Invalid shop config in")
