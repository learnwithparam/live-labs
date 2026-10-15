from invoices.numbering import next_invoice_number


def test_counter_restarts_each_year():
    assert next_invoice_number("FH-2025-00912", 2026) == "FH-2026-00001"
    assert next_invoice_number("FH-2026-00041", 2026) == "FH-2026-00042"
