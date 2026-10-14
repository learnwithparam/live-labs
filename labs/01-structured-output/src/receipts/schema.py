"""The ledger entry a receipt must become.

The JSON schema the model is held to only guarantees shape. The business rule, that the lines add up
to the total, lives in `reconcile`, because no JSON schema can express it.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

CENT = 0.01


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    quantity: float = Field(1, description="Units bought; 1 when the receipt does not say")
    amount: float = Field(description="Line total after quantity; negative for a coupon line")


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: str
    date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code such as USD or EUR")
    line_items: list[LineItem] = Field(min_length=1)
    discount: float = Field(0, ge=0, description="Discounts not already listed as line items")
    tax: float = Field(0, ge=0, description="All tax lines summed")
    tax_included: bool = Field(False, description="True when item prices already include the tax")
    tip: float = Field(0, ge=0)
    total: float

    def expected_total(self) -> float:
        items = sum(item.amount for item in self.line_items)
        tax = 0 if self.tax_included else self.tax
        return round(items - self.discount + tax + self.tip, 2)

    @model_validator(mode="after")
    def reconcile(self) -> "Receipt":
        expected = self.expected_total()
        if abs(expected - self.total) > CENT:
            raise ValueError(mismatch(self.total, expected))
        return self


def mismatch(total: float, expected: float) -> str:
    """The error the model reads on its repair attempt, so it names the likely mistakes."""
    return (
        f"total is {total:.2f} but line items, discount, tax and tip add up to {expected:.2f}; "
        "a discount may be counted twice or a line may be missing"
    )
