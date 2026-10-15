"""The same admin, as MCP tools designed for the merchant's job instead of the API's shape.

Four design moves, each one a fix for a way the endpoint server failed:

1. **Name tools by the job.** `set_price` and `adjust_stock`, not `variant_update` with a dict.
2. **Take what the merchant says.** A SKU, "Colombia 1kg" or "#1043", never an internal ID to chain.
3. **Errors say what to do next.** A tool raises `ToolError`, whose message the SDK passes to the model,
   naming the choices: "KEN-250 or KEN-1K: ask the merchant which one".
4. **Annotate the risk.** Read-only tools say so; refunds are marked destructive, so a client can ask
   the merchant first. Shopify's Sidekick team moved this kind of guidance into the tool responses.
"""

from datetime import date
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from tidepool.store import Store

READ_ONLY = ToolAnnotations(readOnlyHint=True)
SAFE_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
MOVES_MONEY = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


def build(store: Store) -> MCPServer:
    mcp = MCPServer("tidepool-store", instructions=(
        "Tidepool Coffee's store admin. Look products up with find_products before changing them. "
        "Prices and stock belong to a variant, named by its SKU."))

    def find_variant(item: str) -> tuple[dict, dict]:
        """The one variant the merchant means, from a SKU or from their own words."""
        exact = store.variant(item.strip().upper())
        if exact:
            return exact
        # Our first cut took only a SKU, and the model guessed ones that do not exist (COL-1KG,
        # GRINDER-ELECTRIC) instead of looking them up. Now every word must appear in the title,
        # size or SKU, so "Colombia 1kg" and "electric grinder" both find their one variant.
        words = item.lower().replace("-", " ").split()
        matches = [(product, variant) for product in store.products for variant in product["variants"]
                   if all(word in f"{product['title']} {variant['option']} {variant['sku'].replace('-', ' ')}".lower()
                          for word in words)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ToolError(f"Nothing matches {item!r}. Call find_products with one word from the title.")
        # The tool, not the prompt, refuses to pick between sizes: the model cannot guess past it.
        choices = "; ".join(f"{variant['sku']} ({product['title']} {variant['option']}, ${variant['price']:.2f})"
                            for product, variant in matches)
        raise ToolError(f"{item!r} matches {len(matches)} variants: {choices}. "
                        "Ask the merchant which one; change nothing yet.")

    def find_product(handle: str) -> dict:
        product = store.product(handle.strip().lower())
        if not product:
            raise ToolError(f"No product with handle {handle}. Call find_products and use the handle it returns.")
        return product

    def find_order(order_number: str) -> dict:
        order = store.order(order_number)
        if not order:
            known = ", ".join(order["name"] for order in store.orders)
            raise ToolError(f"No order {order_number}. The orders are: {known}.")
        return order

    @mcp.tool(annotations=READ_ONLY)
    def find_products(search: str = "") -> dict:
        """Find products by words in their title, with each variant's SKU, size, price and stock.

        Call this first whenever the merchant names a product. If the merchant did not say which size and
        the product has more than one variant, ask the merchant which one before changing anything.

        Args:
            search: Words from the product title, like "kenya" or "grinder". Empty lists every product.
        """
        words = search.lower().split()
        # One JSON object, not a list: the SDK would send a list as one text block per product.
        return {"products": [{"handle": product["handle"], "title": product["title"], "status": product["status"],
                 "collections": product["collections"],
                 "variants": [{"sku": variant["sku"], "size": variant["option"], "price": variant["price"],
                               "stock": variant["stock"]} for variant in product["variants"]]}
                for product in store.products if all(word in product["title"].lower() for word in words)]}

    @mcp.tool(annotations=SAFE_WRITE)
    def set_price(item: str, price: float) -> dict:
        """Set the price of one variant, in dollars.

        Args:
            item: A SKU like ETH-250, or the product and size in the merchant's words, like "Ethiopia 250g".
            price: The new price in dollars, like 18.5.
        """
        product, variant = find_variant(item)
        if price <= 0:
            raise ToolError("A price must be above zero. To stop selling it, use set_product_status with draft.")
        old_price, variant["price"] = variant["price"], round(price, 2)
        return {"ok": f"{product['title']} {variant['option']} ({variant['sku']}) is now "
                      f"${variant['price']:.2f}, was ${old_price:.2f}."}

    @mcp.tool(annotations=SAFE_WRITE)
    def adjust_stock(item: str, change: int) -> dict:
        """Add or remove stock when goods arrive or leave: +24 for a delivery, -2 for damaged bags.

        To correct the count after counting the shelf, use set_stock instead.

        Args:
            item: A SKU, or the product and size in the merchant's words, like "Colombia 1kg".
            change: How many to add (positive) or remove (negative).
        """
        _, variant = find_variant(item)
        if variant["stock"] + change < 0:
            raise ToolError(f"{variant['sku']} has {variant['stock']} in stock; it cannot go below zero. "
                            "Use set_stock with 0.")
        variant["stock"] += change
        return {"ok": f"{variant['sku']} stock is now {variant['stock']}."}

    @mcp.tool(annotations=SAFE_WRITE)
    def set_stock(item: str, on_hand: int) -> dict:
        """Set the stock to an exact count: after counting the shelf, or 0 to mark a variant sold out.

        Args:
            item: A SKU, or the product and size in the merchant's words, like "Colombia 1kg".
            on_hand: The exact number on the shelf now.
        """
        _, variant = find_variant(item)
        if on_hand < 0:
            raise ToolError("Stock cannot be negative.")
        old_stock, variant["stock"] = variant["stock"], on_hand
        return {"ok": f"{variant['sku']} stock is now {on_hand}, was {old_stock}."}

    @mcp.tool(annotations=SAFE_WRITE)
    def set_product_status(handle: str, status: Literal["active", "draft"]) -> dict:
        """Put a product live on the store (active), or take it off the store (draft).

        A product the merchant calls new, ready or launched is still draft until this runs; reading
        its status with find_products changes nothing. Taking it off keeps its stock and price.

        Args:
            handle: The product handle from find_products, like cold-brew-kit.
            status: active to show it to customers, draft to hide it.
        """
        product = find_product(handle)
        product["status"] = status
        shown = "live on the store" if status == "active" else "hidden from the store"
        return {"ok": f"{product['title']} is now {shown}."}

    @mcp.tool(annotations=SAFE_WRITE)
    def rename_product(handle: str, new_title: str) -> dict:
        """Change the title customers see. The handle stays the same, so links keep working.

        Args:
            handle: The product handle from find_products.
            new_title: The new title.
        """
        product = find_product(handle)
        old_title, product["title"] = product["title"], new_title.strip()
        return {"ok": f"Renamed {old_title} to {product['title']}."}

    @mcp.tool(annotations=SAFE_WRITE)
    def set_collections(handle: str, collections: list[str]) -> dict:
        """Set every collection a product belongs to. Pass the full list it should be in after the change.

        Args:
            handle: The product handle from find_products.
            collections: The collection names, from: Single origin, Blends, Seasonal, Equipment, Decaf.
        """
        product, known = find_product(handle), store.data["collections"]
        unknown = [name for name in collections if name not in known]
        if unknown:
            raise ToolError(f"No collection {', '.join(unknown)}. The collections are: {', '.join(known)}.")
        old_collections, product["collections"] = product["collections"], list(collections)
        return {"ok": f"{product['title']} was in {', '.join(old_collections) or 'none'}; "
                      f"now in {', '.join(collections) or 'none'}."}

    @mcp.tool(annotations=SAFE_WRITE)
    def create_discount(code: str, percent: float, product_handle: str = "", ends_on: str = "") -> dict:
        """Create a percentage discount code for the whole store, or for one product.

        Args:
            code: The code customers type, like WELCOME10.
            percent: The percentage off, like 10 for 10%.
            product_handle: Leave empty for the whole store, or a product handle for one product.
            ends_on: The last day it works, as YYYY-MM-DD. Leave empty for no end date.
        """
        if store.discount(code):
            raise ToolError(f"The code {code.upper()} already exists.")
        if not 0 < percent < 100:
            raise ToolError("percent is a number between 1 and 99, like 15 for 15% off.")
        applies_to = find_product(product_handle)["handle"] if product_handle else "all"
        if ends_on:
            try:
                last_day = date.fromisoformat(ends_on).isoformat()
            except ValueError:
                raise ToolError(f"ends_on must be YYYY-MM-DD, like 2026-12-31, not {ends_on}.")
            if last_day <= store.data["today"]:
                raise ToolError(f"{ends_on} is not after today, {store.data['today']}.")
        store.discounts.append({"code": code.upper(), "percent": int(percent) if percent == int(percent) else percent,
                                "applies_to": applies_to, "ends": ends_on or None, "active": True})
        target = "everything" if applies_to == "all" else applies_to
        return {"ok": f"{code.upper()} gives {percent:g}% off {target}"
                      + (f" until {ends_on}." if ends_on else ", with no end date.")}

    @mcp.tool(annotations=SAFE_WRITE)
    def turn_off_discount(code: str) -> dict:
        """Stop a discount code working. Orders that already used it are not changed.

        Args:
            code: The discount code, like SPRING15.
        """
        discount = store.discount(code)
        if not discount:
            codes = ", ".join(existing["code"] for existing in store.discounts)
            raise ToolError(f"No code {code}. The codes are: {codes}.")
        discount["active"] = False
        return {"ok": f"{discount['code']} no longer works."}

    @mcp.tool(name="find_order", annotations=READ_ONLY)
    def look_up_order(order_number: str) -> dict:
        """Look up an order: customer, items with SKU and price, total, refunds and fulfilment.

        Args:
            order_number: The order number as the merchant says it, like 1043 or #1043.
        """
        order = find_order(order_number)
        return {"order": order["name"], "customer": order["customer"], "fulfillment": order["fulfillment"],
                "refunded": order["refunded"],
                "items": [{"sku": line["sku"], "title": store.variant(line["sku"])[0]["title"],
                           "quantity": line["quantity"], "price": line["price"]} for line in order["lines"]]}

    @mcp.tool(annotations=MOVES_MONEY)
    def refund_items(order_number: str, sku: str, quantity: int = 1) -> dict:
        """Refund some items on an order to the customer's card. Money leaves the store; this cannot be undone.

        Args:
            order_number: The order number, like 1043.
            sku: The SKU of the item to refund, from find_order.
            quantity: How many of that item to refund.
        """
        order = find_order(order_number)
        line = next((line for line in order["lines"] if line["sku"] == sku.upper()), None)
        if not line:
            skus = ", ".join(each["sku"] for each in order["lines"])
            raise ToolError(f"{order['name']} has no {sku}. Its items are: {skus}.")
        if quantity > line["quantity"]:
            raise ToolError(f"{order['name']} has only {line['quantity']} of {line['sku']}.")
        amount = line["price"] * quantity
        order["refunded"] += amount
        return {"ok": f"Refunded ${amount:.2f} to {order['customer']} for {quantity} x {line['sku']} on {order['name']}."}

    @mcp.tool(annotations=SAFE_WRITE)
    def fulfil_order(order_number: str) -> dict:
        """Mark an order as fulfilled once it is packed and handed to the courier.

        Args:
            order_number: The order number, like 1046.
        """
        order = find_order(order_number)
        if order["fulfillment"] == "fulfilled":
            return {"ok": f"{order['name']} was already fulfilled."}
        order["fulfillment"] = "fulfilled"
        return {"ok": f"{order['name']} for {order['customer']} is fulfilled."}

    return mcp
