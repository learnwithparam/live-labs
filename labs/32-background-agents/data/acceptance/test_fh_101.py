from decimal import Decimal

from invoices.money import percent_of


def test_half_a_cent_rounds_up():
    assert percent_of(1050, Decimal("0.05")) == 53
    assert percent_of(1150, Decimal("0.05")) == 58
    assert percent_of(10000, Decimal("0.0825")) == 825
