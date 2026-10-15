"""The tools the agent may call, for one signed-in customer. The rules live here, not in the prompt.

A prompt can be argued with; a tool cannot. Each tool checks the policy in `data/policy.md` and the
customer's ownership of the order, and returns a short result the model reads on its next step. A
refused action comes back as an error the model can explain to the customer, never as an exception.
"""

from datetime import date

from kestrel.shop import Shop

DAMAGE_DAYS, RETURN_DAYS = 14, 30  # P3 and P4
HAND_OFF_OVER = 300.0  # P5: a refund over this goes to a person


class OrderTools:
    def __init__(self, shop: Shop, customer: str):
        # The customer comes from the signed-in session, never from the model, so it cannot be talked into another account.
        self.shop, self.customer = shop, customer

    def _own(self, order_id: str) -> dict | None:
        order = self.shop.order(order_id.strip().upper())
        # P6: someone else's order looks exactly like one that does not exist.
        return order if order and order["customer"] == self.customer else None

    def _days_since_delivery(self, order: dict) -> int | None:
        return (self.shop.today - date.fromisoformat(order["delivered"])).days if order["delivered"] else None

    def find_orders(self) -> dict:
        """List the signed-in customer's orders, newest first, with item, status and total.

        Call this first whenever the customer names an item but not an order number, instead of asking them.
        """
        return {"orders": [{field: order[field] for field in ("id", "item", "status", "total")} for order in self.shop.orders_of(self.customer)]}

    def get_order(self, order_id: str) -> dict:
        """Look up one of the customer's orders: status, address, delivery date and tracking.

        Args:
            order_id: The order number, like KO-1041.
        """
        order = self._own(order_id)
        if not order:
            return {"error": f"No order {order_id} on this customer's account."}
        return {field: order[field] for field in ("id", "item", "status", "total", "address", "delivered", "shipment")}

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order that has not shipped yet. Policy P1.

        Args:
            order_id: The order number to cancel.
        """
        order = self._own(order_id)
        if not order:
            return {"error": f"No order {order_id} on this customer's account."}
        if order["status"] != "processing":
            return {"error": f"{order['id']} is {order['status']}, so it cannot be cancelled (P1). Offer a return after delivery."}
        self.shop.change("cancel", order["id"], status="cancelled", amount=order["total"])
        return {"ok": f"Cancelled {order['id']}. ${order['total']:.2f} goes back to the card in 3 to 5 days."}

    def change_address(self, order_id: str, new_address: str) -> dict:
        """Change the delivery address of an order that has not shipped yet. Policy P2.

        Args:
            order_id: The order number.
            new_address: The full new delivery address, with postcode.
        """
        order = self._own(order_id)
        if not order:
            return {"error": f"No order {order_id} on this customer's account."}
        if order["status"] != "processing":
            return {"error": f"{order['id']} is {order['status']}, so the address cannot change (P2)."}
        self.shop.change("address", order["id"], detail=new_address.strip())
        return {"ok": f"{order['id']} will go to {new_address.strip()}."}

    def refund_damaged_item(self, order_id: str, what_is_damaged: str) -> dict:
        """Refund an order in full because it arrived damaged, within 14 days of delivery. Policy P3.

        Args:
            order_id: The order number.
            what_is_damaged: The damage, in the customer's words.
        """
        order = self._own(order_id)
        if not order:
            return {"error": f"No order {order_id} on this customer's account."}
        days = self._days_since_delivery(order)
        if days is None or order["status"] != "delivered":
            return {"error": f"{order['id']} is {order['status']}; only a delivered order can be refunded as damaged."}
        if days > DAMAGE_DAYS:
            return {"error": f"Delivered {days} days ago; damage must be reported within {DAMAGE_DAYS} days (P3)."}
        # The amount comes from the order, never from the model: it cannot refund more than was paid.
        if order["total"] > HAND_OFF_OVER:
            # The tool hands off itself. In our first review the model told the customer "I'll pass this to
            # a person" and never called hand_off, so nobody would have seen the chat. Policy the system can
            # enforce belongs in the tool, not in a promise the model may forget.
            self.shop.hand_off(f"Damaged {order['item']}, ${order['total']:.2f} refund is over ${HAND_OFF_OVER:.0f} (P5)")
            return {"error": f"${order['total']:.2f} is over ${HAND_OFF_OVER:.0f}, so a person must approve it (P5). "
                             "This chat is now with a person on the support team; tell the customer they reply within 4 hours."}
        self.shop.change("refund", order["id"], status="refunded", amount=order["total"], detail=what_is_damaged)
        return {"ok": f"Refunded ${order['total']:.2f} for {order['id']}. No need to send it back."}

    def start_return(self, order_id: str) -> dict:
        """Start a return for an unused item within 30 days of delivery, and email a return label. Policy P4.

        Args:
            order_id: The order number.
        """
        order = self._own(order_id)
        if not order:
            return {"error": f"No order {order_id} on this customer's account."}
        days = self._days_since_delivery(order)
        if days is None or order["status"] != "delivered":
            return {"error": f"{order['id']} is {order['status']}; only a delivered order can be returned."}
        if days > RETURN_DAYS:
            return {"error": f"Delivered {days} days ago; returns close after {RETURN_DAYS} days (P4)."}
        self.shop.change("return", order["id"], status="return started", amount=order["total"])
        return {"ok": f"Return started for {order['id']}. A label is on its way by email; the refund follows on arrival."}

    def hand_off(self, reason: str) -> dict:
        """Pass the chat to a person on the support team, with the reason. Policy P5.

        Args:
            reason: Why a person must take it, in one sentence.
        """
        self.shop.hand_off(reason)
        return {"ok": "A person on the support team will reply within 4 hours."}

    def all(self) -> list:
        """The tools in the order the model sees them."""
        return [self.find_orders, self.get_order, self.cancel_order, self.change_address,
                self.refund_damaged_item, self.start_return, self.hand_off]
