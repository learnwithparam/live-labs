"""Money is held as integer cents, so totals never pick up float drift.

Amounts arrive as text from the order system and leave as text on the PDF and in the export. Only the
two edges convert, and everything in between adds whole cents.
"""

from decimal import Decimal, InvalidOperation

# Symbols for the currencies Fernhill bills in today. Each has two minor units.
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def parse_amount(text: str) -> int:
    """Turn "19.99" into 1999 cents. Raises ValueError on anything that is not a plain amount."""
    try:
        amount = Decimal(text.strip())
    except InvalidOperation as error:
        raise ValueError(f"not an amount: {text!r}") from error
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError(f"more than two decimal places: {text!r}")
    return int(amount * 100)


def format_money(cents: int, currency: str = "USD") -> str:
    """Render cents for a person: 123456 in USD is "$1,234.56"."""
    symbol = SYMBOLS[currency]
    sign = "-" if cents < 0 else ""
    whole, remainder = divmod(abs(cents), 100)
    return f"{sign}{symbol}{whole:,}.{remainder:02d}"


def percent_of(cents: int, rate: Decimal) -> int:
    """A rate applied to an amount, rounded to whole cents."""
    return round(cents * float(rate))
