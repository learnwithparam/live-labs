from datetime import date
from decimal import Decimal

from invoices.invoice import Invoice, LineItem


def test_discount_before_tax():
    invoice = Invoice("FH-2026-00050", "Harbor Deli", date(2026, 3, 2), lines=[LineItem("Goods", 1, 10000)],
                      discount_percent=Decimal("10"), tax_rate=Decimal("0.08"))
    assert invoice.discount_cents == 1000
    assert invoice.tax_cents == 720
    assert invoice.total_cents == 9720
