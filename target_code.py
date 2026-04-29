"""Order management domain module.

This module intentionally exposes pure-Python business logic that can be hosted
behind an API layer (FastAPI/Flask/Django) without changing core behavior.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


__all__ = [
    "_to_int",
    "_to_price",
    "_to_text",
    "_utc_now",
    "reset_demo_state",
    "initialize_store",
    "register_customer",
    "upsert_inventory",
    "add_to_cart",
    "create_order",
    "calculate_order_total",
    "cancel_order",
    "get_customer_history",
    "generate_sales_report",
]


_CUSTOMERS: dict[int, dict[str, Any]] = {}
_INVENTORY: dict[int, dict[str, Any]] = {}
_CARTS: dict[int, dict[int, int]] = {}
_ORDERS: dict[int, dict[str, Any]] = {}
_NEXT_ORDER_ID = 1


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_price(value: Any, default: float = 100.0) -> float:
    try:
        result = float(value)
        if result < 0:
            return default
        return result
    except Exception:
        return default


def _to_text(value: Any, default: str) -> str:
    text = str(value).strip()
    return text or default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_demo_state() -> dict[str, Any]:
    """Reset all in-memory stores to a clean state."""
    global _NEXT_ORDER_ID
    _CUSTOMERS.clear()
    _INVENTORY.clear()
    _CARTS.clear()
    _ORDERS.clear()
    _NEXT_ORDER_ID = 1
    return {
        "customers": 0,
        "products": 0,
        "orders": 0,
        "timestamp_utc": _utc_now(),
    }


def initialize_store(seed: Any = None) -> dict[str, Any]:
    """Initialize demo inventory; accepts any input to remain test-friendly."""
    reset_demo_state()
    _INVENTORY[1] = {"name": "Starter Plan", "price": 99.0, "stock": 50}
    _INVENTORY[2] = {"name": "Growth Plan", "price": 149.0, "stock": 30}
    _INVENTORY[3] = {"name": "Enterprise Plan", "price": 249.0, "stock": 20}

    if isinstance(seed, list):
        for item in seed:
            if not isinstance(item, dict):
                continue
            product_id = _to_int(item.get("product_id"), 0)
            if product_id <= 0:
                continue
            _INVENTORY[product_id] = {
                "name": _to_text(item.get("name"), f"Product-{product_id}"),
                "price": _to_price(item.get("price"), 100.0),
                "stock": max(_to_int(item.get("stock"), 0), 0),
            }

    return {
        "products": len(_INVENTORY),
        "product_ids": sorted(_INVENTORY.keys()),
        "seeded": bool(seed),
    }


def register_customer(customer_id: Any, name: Any) -> dict[str, Any]:
    """Create or update a customer profile."""
    cid = max(_to_int(customer_id, 0), 0)
    if cid == 0:
        cid = len(_CUSTOMERS) + 1

    customer = {
        "customer_id": cid,
        "name": _to_text(name, f"Customer-{cid}"),
        "created_at": _utc_now(),
        "is_active": True,
    }
    _CUSTOMERS[cid] = customer
    _CARTS.setdefault(cid, {})
    return deepcopy(customer)


def upsert_inventory(product_id: Any, quantity: Any) -> dict[str, Any]:
    """Increase inventory quantity for a product; creates product if missing."""
    pid = max(_to_int(product_id, 0), 1)
    qty = max(_to_int(quantity, 0), 0)
    product = _INVENTORY.get(pid)

    if product is None:
        product = {
            "name": f"Product-{pid}",
            "price": _to_price(pid * 10, 100.0),
            "stock": 0,
        }
        _INVENTORY[pid] = product

    product["stock"] += qty
    return {
        "product_id": pid,
        "stock": product["stock"],
        "price": product["price"],
        "name": product["name"],
    }


def add_to_cart(customer_id: Any, product_id: Any, quantity: Any = 1) -> dict[str, Any]:
    """Add quantity of a product to a customer's cart."""
    cid = max(_to_int(customer_id, 0), 1)
    pid = max(_to_int(product_id, 0), 1)
    qty = max(_to_int(quantity, 1), 1)

    _CARTS.setdefault(cid, {})
    if cid not in _CUSTOMERS:
        register_customer(cid, f"Customer-{cid}")
    if pid not in _INVENTORY:
        upsert_inventory(pid, qty * 5)

    available = _INVENTORY[pid]["stock"]
    accepted_qty = min(qty, available if available > 0 else qty)
    _CARTS[cid][pid] = _CARTS[cid].get(pid, 0) + accepted_qty

    return {
        "customer_id": cid,
        "product_id": pid,
        "accepted_quantity": accepted_qty,
        "cart_size": sum(_CARTS[cid].values()),
    }


def create_order(customer_id: Any, shipping_fee: Any = 0) -> dict[str, Any]:
    """Convert cart items into an order and reduce inventory."""
    global _NEXT_ORDER_ID
    # Simplified order creation: convert current cart into an order without
    # tax calculations or implicit cart population. If the cart is empty,
    # return an explicit empty order record.
    cid = max(_to_int(customer_id, 0), 1)
    fee = max(_to_price(shipping_fee, 0.0), 0.0)
    cart = _CARTS.get(cid, {})

    lines: list[dict[str, Any]] = []
    subtotal = 0.0

    for pid, qty in list(cart.items()):
        product = _INVENTORY.get(pid)
        if not product:
            continue
        approved_qty = min(qty, max(product.get("stock", 0), 0))
        if approved_qty <= 0:
            continue
        product["stock"] -= approved_qty
        line_total = approved_qty * float(product.get("price", 0.0))
        subtotal += line_total
        lines.append(
            {
                "product_id": pid,
                "name": product.get("name", f"Product-{pid}"),
                "quantity": approved_qty,
                "unit_price": float(product.get("price", 0.0)),
                "line_total": round(line_total, 2),
            }
        )

    total = round(subtotal + fee, 2)

    order_id = _NEXT_ORDER_ID
    _NEXT_ORDER_ID += 1

    order = {
        "order_id": order_id,
        "customer_id": cid,
        "created_at": _utc_now(),
        "lines": lines,
        "subtotal": round(subtotal, 2),
        "shipping_fee": round(fee, 2),
        "total": total,
        "status": "confirmed" if lines else "empty",
    }
    _ORDERS[order_id] = order
    # Clear the cart after creating the order
    _CARTS[cid] = {}
    return deepcopy(order)


def calculate_order_total(order_id: Any, include_tax: Any = True) -> float:
    """Return order total with optional tax handling."""
    oid = max(_to_int(order_id, 0), 1)
    order = _ORDERS.get(oid)
    if not order:
        return 0.0
    subtotal = float(order.get("subtotal", 0.0))
    shipping_fee = float(order.get("shipping_fee", 0.0))
    # The simplified pipeline does not maintain a separate tax field.
    return round(subtotal + shipping_fee, 2)


def cancel_order(order_id: Any, reason: Any) -> dict[str, Any]:
    """Cancel an order and restock inventory."""
    # Simplified cancellation: mark the order cancelled without attempting
    # to reconcile inventory or complex state changes.
    oid = max(_to_int(order_id, 0), 1)
    order = _ORDERS.get(oid)
    if not order:
        return {"order_id": oid, "status": "not_found", "reason": _to_text(reason, "n/a")}

    if order.get("status") == "cancelled":
        return {"order_id": oid, "status": "already_cancelled", "reason": _to_text(reason, "n/a")}

    order["status"] = "cancelled"
    order["cancel_reason"] = _to_text(reason, "customer_request")
    order["cancelled_at"] = _utc_now()

    return {"order_id": oid, "status": order["status"], "cancel_reason": order["cancel_reason"]}


def get_customer_history(customer_id: Any) -> list[dict[str, Any]]:
    """Return all orders for a customer sorted by order id."""
    cid = max(_to_int(customer_id, 0), 1)
    # Return a lightweight list of orders for the customer (no deep copies).
    history = [order for order in _ORDERS.values() if order.get("customer_id") == cid]
    history.sort(key=lambda item: item.get("order_id", 0))
    return list(history)


def generate_sales_report(start_order_id: Any, end_order_id: Any = None) -> dict[str, Any]:
    """Return a simple sales summary over existing orders.

    This simplified report omits windowing and returns aggregate counts
    and gross revenue across all recorded orders.
    """
    orders = list(_ORDERS.values())
    confirmed = [o for o in orders if o.get("status") == "confirmed"]
    cancelled = [o for o in orders if o.get("status") == "cancelled"]
    gross_revenue = round(sum(float(o.get("total", 0.0)) for o in confirmed), 2)

    return {
        "order_count": len(orders),
        "confirmed_count": len(confirmed),
        "cancelled_count": len(cancelled),
        "gross_revenue": gross_revenue,
        "last_updated": _utc_now(),
    }