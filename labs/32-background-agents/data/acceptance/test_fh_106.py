from datetime import date

from invoices.invoice import Invoice, LineItem


def test_amount_due_after_payments():
    invoice = Invoice("FH-2026-00060", "Harbor Deli", date(2026, 3, 2), lines=[LineItem("Goods", 1, 10000)],
                      payments_cents=[2500, 2500])
    assert invoice.amount_due_cents == 5000
    invoice.payments_cents.append(9000)
    assert invoice.amount_due_cents == 0
