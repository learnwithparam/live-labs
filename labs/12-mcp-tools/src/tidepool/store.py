"""Tidepool Coffee's store, as its admin API holds it: products, variants, stock, discounts, orders.

This is the source of truth for the lab. Both MCP servers change this object and nothing else, and
the review never reads what the assistant said it did. It reads `changes()`: every field that differs
from the store as it was before the task.
"""

import copy
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "store.json"


class Store:
    def __init__(self, data: dict | None = None):
        # Each task gets a fresh copy, so one task's change can never make another task pass.
        self.data = copy.deepcopy(data or json.loads(DATA.read_text()))
        self.before = self.flat()

    @property
    def products(self) -> list[dict]:
        return self.data["products"]

    @property
    def orders(self) -> list[dict]:
        return self.data["orders"]

    @property
    def discounts(self) -> list[dict]:
        return self.data["discounts"]

    def variant(self, sku: str) -> tuple[dict, dict] | None:
        """The product and variant with this SKU, or None."""
        for product in self.products:
            for variant in product["variants"]:
                if variant["sku"] == sku:
                    return product, variant
        return None

    def product(self, handle: str) -> dict | None:
        return next((product for product in self.products if product["handle"] == handle), None)

    def order(self, name: str) -> dict | None:
        # Merchants say "1043", "#1043" or "order 1043"; the admin stores "#1043".
        wanted = "#" + "".join(char for char in name if char.isdigit())
        return next((order for order in self.orders if order["name"] == wanted), None)

    def discount(self, code: str) -> dict | None:
        return next((discount for discount in self.discounts if discount["code"] == code.upper()), None)

    def flat(self) -> dict:
        """Every field the review can check, as one flat dict of path to value."""
        fields = {}
        for product in self.products:
            fields[f"product:{product['handle']}.status"] = product["status"]
            fields[f"product:{product['handle']}.title"] = product["title"]
            fields[f"product:{product['handle']}.collections"] = sorted(product["collections"])
            for variant in product["variants"]:
                fields[f"variant:{variant['sku']}.price"] = round(float(variant["price"]), 2)
                fields[f"variant:{variant['sku']}.stock"] = variant["stock"]
        for discount in self.discounts:
            fields[f"discount:{discount['code']}"] = dict(discount)
        for order in self.orders:
            fields[f"order:{order['name']}.refunded"] = round(order["refunded"], 2)
            fields[f"order:{order['name']}.fulfillment"] = order["fulfillment"]
        return fields

    def changes(self) -> dict:
        """What the task changed in the store: path to new value."""
        return {path: value for path, value in self.flat().items() if self.before.get(path) != value}
