from datetime import date

from invoices.invoice import Invoice, LineItem
from invoices.late_fees import late_fee_cents


def test_fees_stop_at_ten_percent():
    invoice = Invoice("FH-2026-00070", "Harbor Deli", date(2020, 1, 6), lines=[LineItem("Goods", 1, 20000)])
    assert late_fee_cents(invoice, date(2025, 8, 1)) == 2000
    assert late_fee_cents(invoice, date(2020, 3, 6)) == 300
