"""Halden & Rye Coffee's store, behind the tools the Kettlebay admin gives its assistant.

Every conversation gets its own copy of the store, so a refund in one eval run never changes what
another run reads, and the golden answers stay true however many times the set runs.
"""

import copy
import json
from pathlib import Path

STORE = Path("data/store.json")
# The tools that change the live store. Each one needs the merchant's approval first.
WRITE_TOOLS = ("update_price", "refund_order", "create_discount")


def load_store(path: Path = STORE) -> dict:
    return json.loads(path.read_text())


class Store:
    """One conversation's view of the store. Tools return a dict; a bad call returns an error the model reads."""

    def __init__(self, data: dict, merchant_answer: str = "approved"):
        self.data = copy.deepcopy(data)
        # In an eval run nobody is at the keyboard, so the merchant's answer to request_approval is
        # scripted. Every golden write case is one the merchant asked for, so the answer is yes.
        self.merchant_answer = merchant_answer

    def call(self, name: str, arguments: dict) -> dict:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return {"error": f"There is no tool named {name}."}
        try:
            return handler(**arguments)
        except TypeError as error:
            return {"error": f"Wrong arguments for {name}: {error}"}

    def product(self, sku: str) -> dict | None:
        return next((product for product in self.data["products"] if product["sku"] == sku.strip().upper()), None)

    def order(self, order_id: str) -> dict | None:
        wanted = str(order_id).strip().lstrip("#")
        return next((order for order in self.data["orders"] if order["order_id"] == wanted), None)

    def tool_search_products(self, query: str) -> dict:
        words = [word for word in query.lower().replace(",", " ").split() if word]
        scored = [(sum(word in product["name"].lower() for word in words), product) for product in self.data["products"]]
        best = max((score for score, _ in scored), default=0)
        # Return the closest names, not every product with one shared word, the way an admin search ranks.
        matches = [{"sku": product["sku"], "name": product["name"], "price": product["price"]}
                   for score, product in scored if best and score == best]
        return {"matches": matches}

    def tool_get_inventory(self, sku: str) -> dict:
        product = self.product(sku)
        if product is None:
            return {"error": f"No product with SKU {sku}."}
        return {"sku": product["sku"], "name": product["name"], "in_stock": product["stock"],
                "reorder_at": product["reorder_at"]}

    def tool_get_order(self, order_id: str) -> dict:
        order = self.order(order_id)
        return order if order else {"error": f"No order {order_id}."}

    def tool_get_sales(self, start_date: str, end_date: str) -> dict:
        days = [day for day in self.data["daily_sales"] if start_date <= day["date"] <= end_date]
        return {"start_date": start_date, "end_date": end_date, "days": len(days), "currency": self.data["currency"],
                "revenue": round(sum(day["revenue"] for day in days), 2), "orders": sum(day["orders"] for day in days)}

    def tool_list_low_stock(self) -> dict:
        low = [{"sku": product["sku"], "name": product["name"], "in_stock": product["stock"],
                "reorder_at": product["reorder_at"]}
               for product in self.data["products"] if product["stock"] <= product["reorder_at"]]
        return {"low_stock": low}

    def tool_request_approval(self, action: str, summary: str) -> dict:
        return {"action": action, "summary": summary, "merchant": self.merchant_answer}

    def tool_update_price(self, sku: str, price: float) -> dict:
        product = self.product(sku)
        if product is None:
            return {"error": f"No product with SKU {sku}."}
        old_price, product["price"] = product["price"], float(price)
        return {"sku": product["sku"], "old_price": old_price, "new_price": product["price"]}

    def tool_refund_order(self, order_id: str, amount: float) -> dict:
        order = self.order(order_id)
        if order is None:
            return {"error": f"No order {order_id}."}
        refundable = round(order["total"] - order["refunded"], 2)
        # The payment provider's own rule: never refund more than was paid. It is not the approval check.
        if float(amount) > refundable:
            return {"error": f"Only ${refundable:.2f} of order {order['order_id']} can still be refunded."}
        order["refunded"] = round(order["refunded"] + float(amount), 2)
        return {"order_id": order["order_id"], "refunded": float(amount), "refunded_in_total": order["refunded"]}

    def tool_create_discount(self, code: str, percent: float, skus: list, starts: str, ends: str) -> dict:
        discount = {"code": code.upper(), "percent": float(percent), "skus": skus, "starts": starts, "ends": ends}
        self.data["discounts"].append(discount)
        return {"created": discount}
