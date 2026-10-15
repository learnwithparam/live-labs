from invoices.money import format_money


def test_yen_has_no_minor_unit():
    assert format_money(150000, "JPY") == "¥150,000"
    assert format_money(123456, "USD") == "$1,234.56"
