import csv
import io
from datetime import date

from invoices.export import to_csv
from invoices.invoice import Invoice, LineItem


def test_commas_and_quotes_survive_a_round_trip():
    names = ["Lopez, Hart & Co", 'The "Corner" Shop', "Bluebird Grocers"]
    invoices = [Invoice(f"FH-2026-0000{number}", name, date(2026, 3, 2), lines=[LineItem("Case", 1, 2400)])
                for number, name in enumerate(names, 1)]
    rows = list(csv.reader(io.StringIO(to_csv(invoices))))
    assert rows[0] == ["number", "customer", "issued_on", "due_on", "currency", "total_cents"]
    assert [row[1] for row in rows[1:]] == names
    assert all(len(row) == 6 for row in rows)
