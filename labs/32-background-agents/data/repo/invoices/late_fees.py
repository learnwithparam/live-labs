"""Late fees, as the customer contract states them.

The contract charges 1.5% of the invoice total for each started month past the due date. Finance applies
the fee when it sends a reminder, so the function takes today's date rather than reading the clock.
"""

from datetime import date
from decimal import Decimal

from invoices.invoice import Invoice
from invoices.money import percent_of
from invoices.terms import days_overdue

MONTHLY_RATE = Decimal("0.015")


def late_fee_cents(invoice: Invoice, today: date) -> int:
    """The late fee owed on `invoice` as of `today`, in cents."""
    overdue = days_overdue(invoice.due_on, today)
    if overdue == 0:
        return 0
    # A started month counts in full: one day late is one month of fee.
    months = (overdue + 29) // 30
    return percent_of(invoice.total_cents, MONTHLY_RATE * months)
