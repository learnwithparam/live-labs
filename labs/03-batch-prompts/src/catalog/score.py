"""The catalogue, the retailer's sample, attribute accuracy against it, and who still needs a person."""

import csv
from dataclasses import dataclass
from pathlib import Path

from catalog.fill import Product
from catalog.schema import ATTRIBUTES, Attributes, same


def load_products(path: Path) -> list[Product]:
    with path.open() as f:
        return [Product(**row) for row in csv.DictReader(f)]


def load_supplied(path: Path) -> dict[str, dict]:
    """sku -> the attributes the retailer supplied. A blank cell is one the retailer did not know."""
    with path.open() as f:
        return {row["sku"]: row for row in csv.DictReader(f)}


def accuracy(filled: dict[str, Attributes], supplied: dict[str, dict]) -> tuple[int, int]:
    """Attributes that agree with the retailer, out of every attribute it supplied."""
    right = total = 0
    for sku, row in supplied.items():
        for attribute in ATTRIBUTES:
            if row[attribute] == "":
                continue
            total += 1
            got = filled.get(sku)
            right += got is not None and same(attribute, getattr(got, attribute), row[attribute])
    return right, total


NOT_FOOD = {"household", "personal_care"}


def why_a_person(product: Product, got: Attributes | None) -> str | None:
    """The reason a product cannot go live on its own, or None when it can."""
    if got is None:
        return "no reply filled this product, even when retried on its own"
    # Vegan is a food filter; the category already says whether the product is food.
    if got.vegan == "unknown" and got.category not in NOT_FOOD:
        return f"no ingredients are listed for {product.title!r}, so vegan cannot be set from the text"
    return None


@dataclass
class Tally:
    products: int
    filled: int
    person: list[str]
    right: int
    total: int
    cost: float

    @property
    def per_thousand(self) -> float:
        return self.cost / self.products * 1000


def tally(products: list[Product], filled: dict[str, Attributes], supplied: dict[str, dict], cost: float) -> Tally:
    person = [p.sku for p in products if why_a_person(p, filled.get(p.sku))]
    right, total = accuracy(filled, supplied)
    return Tally(len(products), len(products) - len(person), person, right, total, cost)
