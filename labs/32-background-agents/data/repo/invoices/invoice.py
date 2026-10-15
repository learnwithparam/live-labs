"""An invoice and its totals.

The order of operations matters to an auditor: line totals, then the customer's discount, then sales tax
on what is left, then payments received. Each step rounds to whole cents once.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from invoices.money import percent_of
from invoices.terms import due_date


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: int
    unit_price_cents: int

    @property
    def total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass
class Invoice:
    number: str
    customer: str
    issued_on: date
    terms_days: int = 30
    currency: str = "USD"
    lines: list[LineItem] = field(default_factory=list)
    discount_percent: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    payments_cents: list[int] = field(default_factory=list)

    @property
    def subtotal_cents(self) -> int:
        return sum(line.total_cents for line in self.lines)

    @property
    def tax_cents(self) -> int:
        return percent_of(self.subtotal_cents, self.tax_rate)

    @property
    def discount_cents(self) -> int:
        # Discounts are a percentage of the goods, agreed per customer in their contract.
        return percent_of(self.subtotal_cents + self.tax_cents, self.discount_percent / 100)

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents + self.tax_cents - self.discount_cents

    @property
    def due_on(self) -> date:
        return due_date(self.issued_on, self.terms_days)
