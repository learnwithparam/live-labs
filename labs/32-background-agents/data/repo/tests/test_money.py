import pytest

from invoices.money import format_money, parse_amount, percent_of


def test_parse_amount_reads_dollars_and_cents():
    assert parse_amount("19.99") == 1999
    assert parse_amount(" 7 ") == 700


def test_parse_amount_refuses_fractions_of_a_cent():
    with pytest.raises(ValueError):
        parse_amount("1.005")


def test_format_money_groups_thousands():
    assert format_money(123456) == "$1,234.56"
    assert format_money(-500, "EUR") == "-€5.00"


def test_percent_of_a_round_amount():
    from decimal import Decimal

    assert percent_of(10000, Decimal("0.0825")) == 825
