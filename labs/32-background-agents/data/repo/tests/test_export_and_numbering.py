from datetime import date

from invoices.export import to_csv
from invoices.invoice import Invoice, LineItem
from invoices.numbering import next_invoice_number


def test_csv_has_the_ledger_header_and_one_row_per_invoice():
    invoice = Invoice("FH-2026-00042", "Bluebird Grocers", date(2026, 3, 2),
                      lines=[LineItem("Oat milk, case of 12", 1, 2400)])
    lines = to_csv([invoice]).splitlines()
    assert lines[0] == "number,customer,issued_on,due_on,currency,total_cents"
    assert lines[1] == "FH-2026-00042,Bluebird Grocers,2026-03-02,2026-04-01,USD,2400"


def test_numbers_count_up():
    assert next_invoice_number(None, 2026) == "FH-2026-00001"
    assert next_invoice_number("FH-2026-00041", 2026) == "FH-2026-00042"
