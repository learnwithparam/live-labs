from datetime import date

from invoices.terms import due_date


def test_weekend_due_dates_move_to_monday():
    assert due_date(date(2026, 4, 2), 30) == date(2026, 5, 4)
    assert due_date(date(2026, 4, 3), 30) == date(2026, 5, 4)
    assert due_date(date(2026, 6, 1), 15) == date(2026, 6, 16)
