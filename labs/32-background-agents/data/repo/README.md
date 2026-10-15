# fernhill-invoices

Invoices for Fernhill's wholesale customers: line items, discounts, sales tax, payment terms, late fees
and the CSV export finance downloads each month. Plain Python, no dependencies.

    ruff check .
    python -m pytest -q

CI runs the same two commands on every pull request, from `.github/workflows/ci.yml`.
