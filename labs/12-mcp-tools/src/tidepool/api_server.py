"""The first MCP server most teams ship: the admin API, one tool per endpoint, wrapped as it is.

It looks complete. Every endpoint is there, named the way the API docs name it, taking the IDs the
API takes. That is the trouble: the merchant says "the Kenya 1kg", and the model must chain
`products_list`, then the variant ID, then the inventory item ID and the location ID, before one
stock change. Errors are raised as exceptions, which the MCP SDK hides from the model on purpose.
"""

from mcp.server.mcpserver import MCPServer

from tidepool.store import Store

# The API numbers collections and price rules from fixed offsets, as a real admin's IDs would be.
COLLECTION_ID_BASE = 300
PRICE_RULE_ID_BASE = 900
NEW_PRICE_RULE_ID_BASE = 950


def global_id(kind: str, number: int) -> str:
    return f"gid://tidepool/{kind}/{number}"


def id_number(any_id: str | int) -> int:
    # The API returns global IDs but some endpoints want the number; the model has to know which.
    return int(str(any_id).rsplit("/", 1)[-1])


def build(store: Store) -> MCPServer:
    mcp = MCPServer("tidepool-admin-api")
    store_location_id = store.data["location"]["id"]
    collection_names = store.data["collections"]

    def product_json(product: dict) -> dict:
        # The full resource, as the endpoint returns it: far more than any task needs, all of it tokens.
        return {"id": global_id("Product", product["id"]), "title": product["title"], "handle": product["handle"],
                "status": product["status"].upper(), "vendor": product["vendor"], "tags": product["tags"],
                "body_html": f"<p>{product['title']} from Tidepool Coffee.</p>",
                "created_at": "2026-03-02T09:14:00Z", "published_scope": "web",
                "variants": [{"id": global_id("ProductVariant", variant["id"]), "sku": variant["sku"],
                              "title": variant["option"], "price": f"{variant['price']:.2f}",
                              "inventory_item_id": variant["inventory_item_id"],
                              "inventory_management": "tidepool", "requires_shipping": True}
                             for variant in product["variants"]]}

    def product_by_id(product_id) -> dict:
        product = next((product for product in store.products if product["id"] == id_number(product_id)), None)
        if not product:
            raise ValueError(f"404 Not Found: Product {product_id}")
        return product

    def variant_by_id(variant_id) -> dict:
        for product in store.products:
            for variant in product["variants"]:
                if variant["id"] == id_number(variant_id):
                    return variant
        raise ValueError(f"404 Not Found: ProductVariant {variant_id}")

    def variant_by_inventory_item(inventory_item_id: int) -> dict:
        for product in store.products:
            for variant in product["variants"]:
                if variant["inventory_item_id"] == int(inventory_item_id):
                    return variant
        raise ValueError(f"404 Not Found: InventoryItem {inventory_item_id}")

    def check_location(requested: int) -> None:
        if requested != store_location_id:
            raise ValueError(f"422 Unprocessable Entity: location {requested}")

    def collection_by_id(collection_id) -> str:
        index = int(collection_id) - COLLECTION_ID_BASE
        if not 0 <= index < len(collection_names):
            raise ValueError(f"404 Not Found: Collection {collection_id}")
        return collection_names[index]

    def order_by_id(order_id) -> dict:
        order = next((order for order in store.orders if order["id"] == int(order_id)), None)
        if not order:
            raise ValueError(f"404 Not Found: Order {order_id}")
        return order

    def order_json(order: dict) -> dict:
        first_name, *_, last_name = order["customer"].split()
        total = sum(line["price"] * line["quantity"] for line in order["lines"])
        return {"id": order["id"], "name": order["name"],
                "customer": {"first_name": first_name, "last_name": last_name}, "financial_status": "paid",
                "fulfillment_status": None if order["fulfillment"] == "unfulfilled" else order["fulfillment"],
                "line_items": [{"id": line["id"], "sku": line["sku"], "quantity": line["quantity"],
                                "price": f"{line['price']:.2f}"} for line in order["lines"]],
                "total_price": f"{total:.2f}"}

    @mcp.tool()
    def products_list(title: str = "", status: str = "") -> list[dict]:
        """GET /products. Retrieves a list of products."""
        return [product_json(product) for product in store.products
                if title.lower() in product["title"].lower() and (not status or product["status"] == status.lower())]

    @mcp.tool()
    def product_get(id: str) -> dict:
        """GET /products/{id}. Retrieves a single product."""
        return product_json(product_by_id(id))

    @mcp.tool()
    def product_update(id: str, fields: dict) -> dict:
        """PUT /products/{id}. Updates a product. Writable fields: title, status, body_html, tags."""
        product = product_by_id(id)
        for field, value in fields.items():
            if field == "status":
                if str(value).lower() not in ("active", "draft", "archived"):
                    raise ValueError(f"422 Unprocessable Entity: status {value!r}")
                product["status"] = str(value).lower()
            elif field in ("title", "tags", "body_html"):
                product[field] = value
            else:
                raise ValueError(f"422 Unprocessable Entity: {field}")
        return product_json(product)

    @mcp.tool()
    def variant_update(id: str, fields: dict) -> dict:
        """PUT /variants/{id}. Updates a product variant. Writable fields: price, compare_at_price, sku."""
        variant = variant_by_id(id)
        if "price" in fields:
            variant["price"] = round(float(fields["price"]), 2)
        return {"id": global_id("ProductVariant", variant["id"]), "sku": variant["sku"],
                "price": f"{variant['price']:.2f}"}

    @mcp.tool()
    def locations_list() -> list[dict]:
        """GET /locations. Retrieves a list of locations."""
        return [{"id": store_location_id, "name": store.data["location"]["name"], "active": True}]

    @mcp.tool()
    def inventory_levels_list(inventory_item_ids: str) -> list[dict]:
        """GET /inventory_levels. Retrieves inventory levels for comma-separated inventory_item_ids."""
        return [{"inventory_item_id": int(item_id), "location_id": store_location_id,
                 "available": variant_by_inventory_item(int(item_id))["stock"]}
                for item_id in inventory_item_ids.split(",") if item_id.strip()]

    @mcp.tool()
    def inventory_level_set(inventory_item_id: int, location_id: int, available: int) -> dict:
        """POST /inventory_levels/set. Sets the inventory level for an inventory item at a location."""
        check_location(location_id)
        variant_by_inventory_item(inventory_item_id)["stock"] = available
        return {"inventory_item_id": inventory_item_id, "location_id": location_id, "available": available}

    @mcp.tool()
    def inventory_level_adjust(inventory_item_id: int, location_id: int, available_adjustment: int) -> dict:
        """POST /inventory_levels/adjust. Adjusts the inventory level of an inventory item at a location."""
        check_location(location_id)
        variant = variant_by_inventory_item(inventory_item_id)
        variant["stock"] += available_adjustment
        return {"inventory_item_id": inventory_item_id, "location_id": location_id, "available": variant["stock"]}

    @mcp.tool()
    def custom_collections_list() -> list[dict]:
        """GET /custom_collections. Retrieves a list of custom collections."""
        return [{"id": COLLECTION_ID_BASE + index, "title": name} for index, name in enumerate(collection_names)]

    @mcp.tool()
    def collects_list(product_id: int) -> list[dict]:
        """GET /collects. Retrieves collects (product to collection links) for a product_id."""
        product = product_by_id(product_id)
        # A collect's ID packs the product and the collection, so collect_delete needs only the one number.
        return [{"id": product["id"] * 1000 + collection_names.index(name), "product_id": product["id"],
                 "collection_id": COLLECTION_ID_BASE + collection_names.index(name)}
                for name in product["collections"]]

    @mcp.tool()
    def collect_create(product_id: int, collection_id: int) -> dict:
        """POST /collects. Adds a product to a custom collection."""
        product, name = product_by_id(product_id), collection_by_id(collection_id)
        if name not in product["collections"]:
            product["collections"].append(name)
        return {"product_id": product["id"], "collection_id": int(collection_id)}

    @mcp.tool()
    def collect_delete(id: int) -> dict:
        """DELETE /collects/{id}. Removes a product from a collection."""
        product = product_by_id(int(id) // 1000)
        name = collection_by_id(COLLECTION_ID_BASE + int(id) % 1000)
        if name not in product["collections"]:
            raise ValueError(f"404 Not Found: Collect {id}")
        product["collections"].remove(name)
        return {}

    @mcp.tool()
    def price_rules_list() -> list[dict]:
        """GET /price_rules. Retrieves a list of price rules."""
        return [{"id": PRICE_RULE_ID_BASE + index, "title": discount["code"], "value_type": "percentage",
                 "value": f"-{discount['percent']}.0",
                 "target_selection": "all" if discount["applies_to"] == "all" else "entitled",
                 "ends_at": discount["ends"], "starts_at": "2026-03-01T00:00:00Z" if discount["active"] else None}
                for index, discount in enumerate(store.discounts)]

    @mcp.tool()
    def price_rule_create(title: str, value_type: str, value: str, target_type: str, target_selection: str,
                          allocation_method: str, customer_selection: str, starts_at: str,
                          ends_at: str = "", entitled_product_ids: list[int] | None = None) -> dict:
        """POST /price_rules. Creates a price rule. Create a discount code for it with discount_code_create."""
        if value_type != "percentage" or not str(value).startswith("-"):
            raise ValueError("422 Unprocessable Entity: value")
        if target_selection == "entitled" and not entitled_product_ids:
            raise ValueError("422 Unprocessable Entity: entitled_product_ids")
        applies_to = "all" if target_selection == "all" else product_by_id(entitled_product_ids[0])["handle"]
        # A price rule alone gives no discount: the store holds it until a code is created for it.
        pending_rules = store.data.setdefault("_rules", [])
        pending_rules.append({"title": title, "percent": abs(float(value)), "applies_to": applies_to,
                              "ends": ends_at[:10] or None})
        return {"id": NEW_PRICE_RULE_ID_BASE + len(pending_rules) - 1, "title": title}

    @mcp.tool()
    def discount_code_create(price_rule_id: int, code: str) -> dict:
        """POST /price_rules/{price_rule_id}/discount_codes. Creates a discount code."""
        pending_rules = store.data.get("_rules", [])
        index = price_rule_id - NEW_PRICE_RULE_ID_BASE
        if not 0 <= index < len(pending_rules):
            raise ValueError(f"404 Not Found: PriceRule {price_rule_id}")
        rule = pending_rules[index]
        percent = rule["percent"]
        store.discounts.append({"code": code.upper(), "percent": int(percent) if percent == int(percent) else percent,
                                "applies_to": rule["applies_to"], "ends": rule["ends"], "active": True})
        return {"code": code.upper(), "price_rule_id": price_rule_id}

    @mcp.tool()
    def price_rule_update(id: int, fields: dict) -> dict:
        """PUT /price_rules/{id}. Updates a price rule. Writable fields: title, ends_at, starts_at."""
        index = id - PRICE_RULE_ID_BASE
        if not 0 <= index < len(store.discounts):
            raise ValueError(f"404 Not Found: PriceRule {id}")
        discount = store.discounts[index]
        if "ends_at" in fields:
            discount["ends"] = (fields["ends_at"] or "")[:10] or None
            # A rule whose end date has passed no longer applies.
            if discount["ends"] and discount["ends"] <= store.data["today"]:
                discount["active"] = False
        if "starts_at" in fields and not fields["starts_at"]:
            discount["active"] = False
        return {"id": id}

    @mcp.tool()
    def orders_list(name: str = "") -> list[dict]:
        """GET /orders. Retrieves a list of orders."""
        return [order_json(order) for order in store.orders if name in order["name"]]

    @mcp.tool()
    def refund_create(order_id: int, refund_line_items: list[dict]) -> dict:
        """POST /orders/{order_id}/refunds. Creates a refund. refund_line_items: [{line_item_id, quantity}]."""
        order = order_by_id(order_id)
        amount = 0.0
        for requested in refund_line_items:
            line = next((line for line in order["lines"] if line["id"] == int(requested["line_item_id"])), None)
            if not line:
                raise ValueError(f"422 Unprocessable Entity: line_item_id {requested.get('line_item_id')}")
            amount += line["price"] * int(requested.get("quantity", 1))
        order["refunded"] += amount
        return {"order_id": order["id"], "transactions": [{"kind": "refund", "amount": f"{amount:.2f}"}]}

    @mcp.tool()
    def fulfillment_create(order_id: int, location_id: int) -> dict:
        """POST /orders/{order_id}/fulfillments. Creates a fulfillment."""
        check_location(location_id)
        order = order_by_id(order_id)
        order["fulfillment"] = "fulfilled"
        return {"order_id": order["id"], "status": "success"}

    return mcp
