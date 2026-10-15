from datetime import date

from invoices.invoice import Invoice, LineItem
from invoices.late_fees import late_fee_cents
from invoices.terms import days_overdue, due_date


def test_net_fifteen():
    assert due_date(date(2026, 6, 1), 15) == date(2026, 6, 16)


def test_days_overdue_is_never_negative():
    assert days_overdue(date(2026, 6, 16), date(2026, 6, 1)) == 0
    assert days_overdue(date(2026, 6, 16), date(2026, 6, 20)) == 4


def test_one_day_late_is_one_month_of_fee():
    invoice = Invoice("FH-2026-00007", "Harbor Deli", date(2026, 1, 5), terms_days=30,
                      lines=[LineItem("Cold brew keg", 2, 10000)])
    assert late_fee_cents(invoice, date(2026, 2, 5)) == 300
