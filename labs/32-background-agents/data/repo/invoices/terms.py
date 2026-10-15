"""Payment terms: when an invoice is due.

Fernhill bills on net terms (net 15, net 30). The due date is the issue date plus the term, and the
customer's bank only moves money on weekdays.
"""

from datetime import date, timedelta


def due_date(issued_on: date, terms_days: int) -> date:
    """The day payment is due for an invoice issued on `issued_on` with net `terms_days`."""
    if terms_days < 0:
        raise ValueError("terms cannot be negative")
    return issued_on + timedelta(days=terms_days)


def days_overdue(due_on: date, today: date) -> int:
    """Whole days past the due date, and 0 for an invoice that is not yet late."""
    return max(0, (today - due_on).days)
