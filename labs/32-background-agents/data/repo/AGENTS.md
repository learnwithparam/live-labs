# Working in fernhill-invoices

- Money is integer cents. Never store a float amount.
- Run `ruff check .` and `python -m pytest -q` before you finish. CI runs exactly these.
- Put the regression test for an issue in a new file named after it, such as `tests/test_fh_101.py`, so
  two pull requests never edit the same test file.
- Do not edit `.github/` or delete a test to make CI pass.
