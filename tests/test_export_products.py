from export_products import _extract_price, _extract_stock


def test_extract_price_from_wrapped_price_object():
    price_info = {"price": {"price": "1500", "old_price": "1800"}}
    price, old_price = _extract_price(price_info)
    assert price == "1500"
    assert old_price == "1800"


def test_extract_price_missing_fields():
    assert _extract_price({}) == ("", "")


def test_extract_stock_sums_present_across_warehouses():
    stock_info = {"stocks": [{"present": 3, "type": "fbo"}, {"present": 7, "type": "fbs"}]}
    assert _extract_stock(stock_info) == "10"


def test_extract_stock_empty():
    assert _extract_stock({"stocks": []}) == "0"
    assert _extract_stock({}) == "0"
