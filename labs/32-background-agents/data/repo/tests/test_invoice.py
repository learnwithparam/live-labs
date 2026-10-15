from datetime import date
from decimal import Decimal

from invoices.invoice import Invoice, LineItem


def sample_invoice(**changes) -> Invoice:
    fields = {"number": "FH-2026-00042", "customer": "Bluebird Grocers", "issued_on": date(2026, 3, 2),
              "lines": [LineItem("Oat milk, case of 12", 4, 2400), LineItem("Delivery", 1, 1500)]}
    return Invoice(**(fields | changes))


def test_totals_without_tax_or_discount():
    invoice = sample_invoice()
    assert invoice.subtotal_cents == 11100
    assert invoice.total_cents == 11100


def test_tax_is_added_to_the_total():
    invoice = sample_invoice(tax_rate=Decimal("0.10"))
    assert invoice.tax_cents == 1110
    assert invoice.total_cents == 12210


def test_due_on_a_weekday():
    # 2 March 2026 is a Monday; net 30 lands on Wednesday 1 April.
    assert sample_invoice().due_on == date(2026, 4, 1)
