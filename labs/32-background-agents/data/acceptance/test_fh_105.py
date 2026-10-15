import pytest

from invoices.money import parse_amount


def test_thousands_separators():
    assert parse_amount("1,234.50") == 123450
    assert parse_amount("12,000") == 1200000
    with pytest.raises(ValueError):
        parse_amount("1,234.505")
    with pytest.raises(ValueError):
        parse_amount("twelve")
