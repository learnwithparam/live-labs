"""The attributes a grocery storefront filters on, one shape for the model, the retailer's sample and the score."""

from typing import Literal

from pydantic import BaseModel

ATTRIBUTES = ["category", "size_value", "size_unit", "organic", "vegan"]
Category = Literal["produce", "dairy", "bakery", "meat_seafood", "pantry", "snacks", "beverages", "frozen", "household", "personal_care"]


class Attributes(BaseModel):
    sku: str
    category: Category
    size_value: float
    size_unit: Literal["g", "ml", "count"]
    organic: bool
    vegan: Literal["yes", "no", "unknown", "not_food"]


class Pack(BaseModel):
    """One reply for many products: each sku once, in any order."""

    products: list[Attributes]


def same(attribute: str, got: object, supplied: str) -> bool:
    """Compare with the retailer's CSV text. A size within 2% matches: 5.0 oz is 141.75 g, filed as 142."""
    if attribute == "size_value":
        return abs(float(got) - float(supplied)) <= 0.02 * float(supplied)
    if attribute == "organic":
        return str(got).lower() == supplied
    return got == supplied
