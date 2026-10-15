"""The monthly CSV that finance downloads and loads into the general ledger.

One row per invoice. The ledger import is strict, so the header and column order are a contract.
"""

from invoices.invoice import Invoice

HEADER = ["number", "customer", "issued_on", "due_on", "currency", "total_cents"]


def to_csv(invoices: list[Invoice]) -> str:
    """Every invoice as one CSV row, with a header, ending in a newline."""
    rows = [",".join(HEADER)]
    for invoice in invoices:
        values = [invoice.number, invoice.customer, invoice.issued_on.isoformat(),
                  invoice.due_on.isoformat(), invoice.currency, str(invoice.total_cents)]
        rows.append(",".join(values))
    return "\n".join(rows) + "\n"
