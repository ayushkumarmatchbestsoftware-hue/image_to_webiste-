"""
Commerce — catalogue, cart pricing, orders.

The doc asks for "payments, orders, customers and transactions". This module is
the part that holds the money and the truth about it, so a few rules are
non-negotiable here in a way they are not elsewhere in this codebase:

  Money is an integer of minor units.  Paise, cents. Never a float — 0.1 + 0.2
  is not 0.3 in binary floating point, and a storefront that is a rounding
  error out on every order is worse than one that cannot take orders at all.

  The SERVER prices the order.  The browser sends product ids and quantities
  and nothing else that touches money. If a client could send its own price, a
  buyer could edit it to 1 paisa in devtools and the seller would ship the
  goods. Prices come from the stored catalogue every time, and the client's
  own total is only ever compared, never trusted.

  Orders survive a restart.  The rest of the local stack keeps state in memory
  because losing a preview costs nothing. An order is a real obligation between
  a buyer and a seller, so it is written to disk, atomically, before the API
  returns success.

  Status changes are a state machine.  A delivered order cannot go back to
  pending; a cancelled one cannot ship. Invalid transitions raise rather than
  silently corrupt the record.
"""
import json
import logging
import os
import re
import threading
import time
import uuid

logger = logging.getLogger("commerce")

STORE_DIR = os.getenv(
    "COMMERCE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "local_store", "commerce"))

_lock = threading.RLock()

# ── Money ────────────────────────────────────────────────────────────────────

# Symbol / code -> (ISO code, minor units per major). Kept small and explicit;
# an unknown currency is better rejected than guessed at.
CURRENCIES = {
    "₹": ("INR", 100), "rs": ("INR", 100), "rs.": ("INR", 100), "inr": ("INR", 100),
    "$": ("USD", 100), "usd": ("USD", 100),
    "€": ("EUR", 100), "eur": ("EUR", 100),
    "£": ("GBP", 100), "gbp": ("GBP", 100),
}
DEFAULT_CURRENCY = os.getenv("STORE_CURRENCY", "INR")

_NUM = re.compile(r"(\d[\d,]*)(?:[.,](\d{1,2}))?")


def parse_price(text) -> tuple:
    """
    Turn a seller's free-text price into (minor_units, currency_code).

    Returns (None, currency) when there is no number in it. Sellers type
    "mid range" or "DM for price" as often as they type a figure, and that is a
    legitimate state — the product is then enquiry-only rather than priced at
    zero, which would let someone order it for nothing.
    """
    s = str(text or "").strip()
    if not s:
        return None, DEFAULT_CURRENCY

    currency = DEFAULT_CURRENCY
    low = s.lower()
    for token, (code, _) in CURRENCIES.items():
        if token in low:
            currency = code
            break

    m = _NUM.search(s.replace(" ", ""))
    if not m:
        return None, currency
    whole = int(m.group(1).replace(",", ""))
    frac = m.group(2) or ""
    minor_per = 100
    minor = whole * minor_per + int((frac + "00")[:2]) if frac else whole * minor_per
    return minor, currency


def format_money(minor, currency=DEFAULT_CURRENCY) -> str:
    """Render minor units for display. Grouping only, no locale guessing."""
    if minor is None:
        return ""
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    return f"{sym}{minor // 100:,}" + (f".{minor % 100:02d}" if minor % 100 else "")


# ── Catalogue ────────────────────────────────────────────────────────────────

def build_catalogue(data: dict, spec: dict, price: str, shots: dict) -> dict:
    """
    The priced, orderable version of what generation produced.

    One product today, because the pipeline is built around a single uploaded
    photo — but shaped as a list so a multi-product seller needs no migration.
    """
    minor, currency = parse_price(price)
    info = data.get("site_info", {}) or {}
    title = (info.get("site_title") or info.get("display_name") or "Product").strip()
    about = (data.get("about", {}) or {}).get("description", "")
    home = data.get("home", {}) or {}

    item = {
        "id": "p1",
        "name": title,
        "blurb": (home.get("subtitle") or info.get("tagline") or about or "")[:240],
        "price_minor": minor,          # None => enquiry only
        "currency": currency,
        "image": shots.get("square") or shots.get("hero") or shots.get("wide"),
        "sub_type": spec.get("sub_type", ""),
        "material": spec.get("material", ""),
        "in_stock": True,
    }
    return {"currency": currency, "items": [item],
            "orderable": bool(minor)}


# ── Durable store ────────────────────────────────────────────────────────────

def _path(website_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(website_id))[:64]
    return os.path.join(STORE_DIR, f"{safe}.json")


def _read(website_id: str) -> dict:
    try:
        with open(_path(website_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"catalogue": {}, "orders": []}


def _write(website_id: str, doc: dict) -> None:
    """
    Write via a temp file and replace, so a crash mid-write cannot leave a
    half-written orders file — which would lose every order, not just the one
    being added.
    """
    os.makedirs(STORE_DIR, exist_ok=True)
    path = _path(website_id)
    tmp = path + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save_catalogue(website_id: str, catalogue: dict) -> None:
    with _lock:
        doc = _read(website_id)
        doc["catalogue"] = catalogue
        _write(website_id, doc)
    logger.info(f"catalogue saved for {website_id[:8]}: "
                f"{len(catalogue.get('items', []))} item(s), "
                f"orderable={catalogue.get('orderable')}")


def get_catalogue(website_id: str) -> dict:
    with _lock:
        return _read(website_id).get("catalogue") or {}


# ── Orders ───────────────────────────────────────────────────────────────────

STATUSES = ("pending", "confirmed", "packed", "shipped", "delivered", "cancelled")
# Where each status may legally go next. Terminal states go nowhere.
TRANSITIONS = {
    "pending":   {"confirmed", "cancelled"},
    "confirmed": {"packed", "cancelled"},
    "packed":    {"shipped", "cancelled"},
    "shipped":   {"delivered"},
    "delivered": set(),
    "cancelled": set(),
}
PAYMENT_STATES = ("unpaid", "pending", "paid", "failed", "refunded")


class CommerceError(Exception):
    """A request that cannot be honoured — bad item, empty cart, bad transition."""


def price_cart(website_id: str, lines: list) -> dict:
    """
    Price a cart from the SERVER's catalogue.

    `lines` is whatever the browser sent. Only `id` and `qty` are read from it;
    every figure is looked up here. This is the single place that decides what
    an order costs.
    """
    cat = get_catalogue(website_id)
    items = {i["id"]: i for i in cat.get("items", [])}
    if not items:
        raise CommerceError("this site has no catalogue yet")

    priced, subtotal = [], 0
    for line in (lines or []):
        pid = str((line or {}).get("id", ""))
        item = items.get(pid)
        if not item:
            raise CommerceError(f"unknown product {pid!r}")
        if item.get("price_minor") is None:
            raise CommerceError(f"{item['name']} is enquiry-only and cannot be ordered")
        try:
            qty = int((line or {}).get("qty", 1))
        except (TypeError, ValueError):
            raise CommerceError("quantity must be a whole number")
        if not 1 <= qty <= 99:
            raise CommerceError("quantity must be between 1 and 99")
        amount = item["price_minor"] * qty
        subtotal += amount
        priced.append({"id": pid, "name": item["name"], "qty": qty,
                       "unit_minor": item["price_minor"],
                       "amount_minor": amount})
    if not priced:
        raise CommerceError("the cart is empty")
    return {"lines": priced, "subtotal_minor": subtotal,
            "total_minor": subtotal,
            "currency": cat.get("currency", DEFAULT_CURRENCY)}


def _valid_contact(customer: dict) -> dict:
    """
    A seller needs a name and one way to reach the buyer. Anything more is
    optional — a long form is how a small storefront loses the sale.
    """
    name = str(customer.get("name", "")).strip()
    phone = re.sub(r"[^\d+]", "", str(customer.get("phone", "")))
    email = str(customer.get("email", "")).strip()
    if len(name) < 2:
        raise CommerceError("please give a name")
    if not phone and not email:
        raise CommerceError("please give a phone number or an email address")
    if phone and not 7 <= len(phone.lstrip("+")) <= 15:
        raise CommerceError("that phone number does not look right")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise CommerceError("that email address does not look right")
    return {"name": name[:80], "phone": phone[:20], "email": email[:120],
            "address": str(customer.get("address", "")).strip()[:400],
            "note": str(customer.get("note", "")).strip()[:400]}


def create_order(website_id: str, lines: list, customer: dict,
                 payment_method: str = "cod",
                 idempotency_key: str = "") -> dict:
    """
    Record an order. Returns the stored order.

    Idempotent on `idempotency_key`: a double-tapped Place Order button, or a
    retry after a dropped connection, returns the ORIGINAL order rather than
    creating a second one. Duplicate orders are the classic checkout bug and
    they cost the seller real money to unpick.
    """
    contact = _valid_contact(customer or {})
    priced = price_cart(website_id, lines)

    with _lock:
        doc = _read(website_id)
        if idempotency_key:
            for o in doc.get("orders", []):
                if o.get("idempotency_key") == idempotency_key:
                    logger.info(f"idempotent replay of order {o['id']}")
                    return o

        seq = len(doc.get("orders", [])) + 1
        order = {
            "id": f"{time.strftime('%y%m%d')}-{seq:04d}",
            "ref": uuid.uuid4().hex[:12],
            "website_id": website_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "customer": contact,
            "lines": priced["lines"],
            "subtotal_minor": priced["subtotal_minor"],
            "total_minor": priced["total_minor"],
            "currency": priced["currency"],
            "status": "pending",
            "payment_method": payment_method,
            "payment_status": "unpaid",
            "idempotency_key": idempotency_key or None,
            "history": [{"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "to": "pending"}],
        }
        doc.setdefault("orders", []).append(order)
        _write(website_id, doc)
    logger.info(f"order {order['id']} placed on {website_id[:8]} "
                f"for {format_money(order['total_minor'], order['currency'])}")
    return order


def list_orders(website_id: str, status: str = "") -> list:
    with _lock:
        orders = _read(website_id).get("orders", [])
    if status:
        orders = [o for o in orders if o.get("status") == status]
    return sorted(orders, key=lambda o: o.get("created_at", ""), reverse=True)


def get_order(website_id: str, order_id: str) -> dict:
    with _lock:
        for o in _read(website_id).get("orders", []):
            if o["id"] == order_id or o.get("ref") == order_id:
                return o
    return {}


def set_status(website_id: str, order_id: str, to: str) -> dict:
    """Move an order along. Refuses transitions the state machine forbids."""
    if to not in STATUSES:
        raise CommerceError(f"unknown status {to!r}")
    with _lock:
        doc = _read(website_id)
        for o in doc.get("orders", []):
            if o["id"] != order_id and o.get("ref") != order_id:
                continue
            cur = o.get("status", "pending")
            if to == cur:
                return o
            if to not in TRANSITIONS.get(cur, set()):
                raise CommerceError(f"an order that is {cur} cannot become {to}")
            o["status"] = to
            o.setdefault("history", []).append(
                {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "to": to})
            _write(website_id, doc)
            logger.info(f"order {order_id}: {cur} -> {to}")
            return o
    raise CommerceError("no such order")


def set_payment(website_id: str, order_id: str, state: str,
                reference: str = "") -> dict:
    if state not in PAYMENT_STATES:
        raise CommerceError(f"unknown payment state {state!r}")
    with _lock:
        doc = _read(website_id)
        for o in doc.get("orders", []):
            if o["id"] != order_id and o.get("ref") != order_id:
                continue
            o["payment_status"] = state
            if reference:
                o["payment_reference"] = str(reference)[:120]
            _write(website_id, doc)
            logger.info(f"order {order_id}: payment {state}")
            return o
    raise CommerceError("no such order")


def summary(website_id: str) -> dict:
    """Counts and revenue for the merchant dashboard."""
    orders = list_orders(website_id)
    paid = [o for o in orders if o.get("payment_status") == "paid"]
    live = [o for o in orders if o.get("status") != "cancelled"]
    cat = get_catalogue(website_id)
    return {
        "orders": len(orders),
        "open": len([o for o in orders if o.get("status") in ("pending", "confirmed", "packed")]),
        "revenue_minor": sum(o["total_minor"] for o in paid),
        "committed_minor": sum(o["total_minor"] for o in live),
        "currency": cat.get("currency", DEFAULT_CURRENCY),
        "by_status": {s: len([o for o in orders if o.get("status") == s])
                      for s in STATUSES},
    }


# ── Seller settings ──────────────────────────────────────────────────────────

def get_settings(website_id: str) -> dict:
    with _lock:
        return _read(website_id).get("settings") or {}


def save_settings(website_id: str, patch: dict) -> dict:
    """
    Merge seller settings (UPI ID, payee name, notification target).

    Kept beside the orders rather than on the website document because these
    outlive a regeneration: a seller who re-runs generation should not have to
    type their UPI ID in again.
    """
    allowed = ("upi_vpa", "upi_name", "site_name",
               "notify_phone", "notify_email", "notify_webhook")
    with _lock:
        doc = _read(website_id)
        cur = doc.get("settings") or {}
        for k in allowed:
            if k in (patch or {}):
                cur[k] = str(patch[k]).strip()[:120]
        doc["settings"] = cur
        _write(website_id, doc)
    logger.info(f"settings saved for {website_id[:8]}: {sorted(cur)}")
    return cur


def claim_payment(website_id: str, order_id: str, reference: str) -> dict:
    """
    The buyer says they have paid, and gives a reference.

    This moves the order to `pending`, never to `paid`. A UPI deep link returns
    nothing to us, so the only honest reading of a buyer's claim is that it is a
    claim. The seller confirms it against their bank.
    """
    from core.payments import clean_reference
    ref = clean_reference(reference)
    with _lock:
        doc = _read(website_id)
        for o in doc.get("orders", []):
            if o["id"] != order_id and o.get("ref") != order_id:
                continue
            if o.get("payment_status") == "paid":
                return o                      # already settled; nothing to claim
            o["payment_status"] = "pending"
            o["payment_reference"] = ref
            o.setdefault("history", []).append(
                {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "to": o.get("status", "pending"), "note": f"buyer claims paid ({ref})"})
            _write(website_id, doc)
            logger.info(f"order {order_id}: buyer claims payment, ref {ref}")
            return o
    raise CommerceError("no such order")


def set_price(website_id: str, price_text: str, item_id: str = "p1") -> dict:
    """
    Set or change what an item costs, after generation.

    Most sellers do not type a price into the generator, which left the site
    permanently enquiry-only with no way back and no explanation — the cart
    simply never appeared. This is the way back.
    """
    minor, currency = parse_price(price_text)
    with _lock:
        doc = _read(website_id)
        cat = doc.get("catalogue") or {}
        items = cat.get("items") or []
        if not items:
            raise CommerceError("this site has no catalogue yet")
        found = False
        for it in items:
            if it.get("id") == item_id:
                it["price_minor"] = minor
                it["currency"] = currency
                found = True
        if not found:
            raise CommerceError(f"unknown product {item_id!r}")
        cat["currency"] = currency
        cat["orderable"] = any(i.get("price_minor") for i in items)
        doc["catalogue"] = cat
        _write(website_id, doc)
    logger.info(f"price for {website_id[:8]} -> {format_money(minor, currency) or 'enquiry only'}")
    return cat
