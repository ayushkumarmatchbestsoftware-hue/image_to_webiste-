"""Catalogue, cart, orders, payments and the order desk."""
import asyncio
import io
import os
import re
import uuid
import zipfile

from fastapi import (APIRouter, Request, Form, File, UploadFile, HTTPException,
                     BackgroundTasks)
from fastapi.responses import JSONResponse, HTMLResponse, Response

from api import ROOT
from core import sites as _sites
from api.deps import log, UI_DIR, FRONTEND, DEV_USER_ID
from config import Config
from core.storage import save as store_save, load as store_load

router = APIRouter(tags=["Commerce & Orders"])
from api.deps import (rate_ok, merchant_ok, summary_display,
                      RATE_MAX, ATTEMPT_MAX)
from core import commerce as _shop
from core import payments as _pay


@router.get("/shop/{website_id}", response_class=HTMLResponse)
async def shop_dashboard(website_id: str):
    """The merchant's order desk. Served as a plain page; it talks to the API."""
    path = os.path.join(UI_DIR, "orders.html")
    with open(path, encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@router.get("/api/{website_id}/shop")
async def shop_info(website_id: str):
    """Everything the storefront needs to render a cart and a checkout."""
    cat = _shop.get_catalogue(website_id)
    settings = _shop.get_settings(website_id)
    items = []
    for i in cat.get("items", []):
        items.append({**i, "price_display": _shop.format_money(
            i.get("price_minor"), i.get("currency", cat.get("currency", "INR")))})
    return {"orderable": bool(cat.get("orderable")),
            "currency": cat.get("currency", "INR"),
            "items": items,
            "methods": _pay.methods_for(settings)}


@router.post("/api/{website_id}/quote")
async def shop_quote(website_id: str, request: Request):
    """
    Price a cart without placing an order.

    The cart page calls this instead of adding its numbers up locally, so what
    the buyer is shown is the same figure the server will charge.
    """
    body = await request.json()
    try:
        q = _shop.price_cart(website_id, body.get("lines") or [])
    except _shop.CommerceError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    q["total_display"] = _shop.format_money(q["total_minor"], q["currency"])
    for ln in q["lines"]:
        ln["amount_display"] = _shop.format_money(ln["amount_minor"], q["currency"])
        ln["unit_display"] = _shop.format_money(ln["unit_minor"], q["currency"])
    return q


@router.post("/api/{website_id}/orders")
async def place_order(website_id: str, request: Request):
    """Place an order. Public, rate limited, server-priced."""
    client = (request.client.host if request.client else "?") + ":" + website_id
    if not rate_ok("attempts", client, ATTEMPT_MAX):
        return JSONResponse(status_code=429, content={
            "error": "too many requests from here just now — please wait a moment"})
    # Checked but NOT recorded yet: only an order that is actually created
    # should spend this budget, so a rejected form does not lock the buyer out.
    if not rate_ok("orders", client, RATE_MAX, record=False):
        return JSONResponse(status_code=429, content={
            "error": "too many orders from here just now — please wait a moment"})

    body = await request.json()
    try:
        order = _shop.create_order(
            website_id,
            lines=body.get("lines") or [],
            customer=body.get("customer") or {},
            payment_method=str(body.get("payment_method", "cod")),
            idempotency_key=str(body.get("idempotency_key", ""))[:64],
        )
        note = _pay.instructions(_shop.get_settings(website_id), order)
    except (_shop.CommerceError, _pay.PaymentError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    rate_ok("orders", client, RATE_MAX)   # the order stuck — now count it

    # Tell the seller. Best-effort and on its own thread: the order is already
    # durable, and a webhook that is down must not turn a completed checkout
    # into an error for the buyer.
    try:
        from core import notify as _notify
        _notify.order_placed(order, _shop.get_settings(website_id),
                             _sites.record(website_id).get("site_name", ""))
    except Exception as e:
        log.warning(f"order notification skipped: {e}")

    return {"success": True, "order": {
        "id": order["id"], "ref": order["ref"],
        "total_display": _shop.format_money(order["total_minor"], order["currency"]),
        "status": order["status"], "payment_status": order["payment_status"],
    }, "payment": note}


@router.post("/api/{website_id}/orders/{order_id}/paid")
async def claim_paid(website_id: str, order_id: str, request: Request):
    """
    The buyer says they have paid and gives a reference.

    Moves the order to `pending`, never `paid` — a UPI deep link tells us
    nothing, so only the seller checking their bank can settle it.
    """
    body = await request.json()
    try:
        o = _shop.claim_payment(website_id, order_id, body.get("reference", ""))
    except (_shop.CommerceError, _pay.PaymentError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"success": True, "payment_status": o["payment_status"]}


@router.get("/api/{website_id}/orders")
async def merchant_orders(website_id: str, request: Request, status: str = ""):
    if not merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    orders = _shop.list_orders(website_id, status)
    for o in orders:
        o["total_display"] = _shop.format_money(o["total_minor"], o["currency"])
    return {"orders": orders, "summary": summary_display(website_id)}


def summary_display(website_id: str) -> dict:
    s = _shop.summary(website_id)
    s["revenue_display"] = _shop.format_money(s["revenue_minor"], s["currency"])
    s["committed_display"] = _shop.format_money(s["committed_minor"], s["currency"])
    return s


@router.patch("/api/{website_id}/orders/{order_id}")
async def update_order(website_id: str, order_id: str, request: Request):
    if not merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    body = await request.json()
    try:
        if body.get("status"):
            o = _shop.set_status(website_id, order_id, body["status"])
        elif body.get("payment_status"):
            o = _shop.set_payment(website_id, order_id, body["payment_status"],
                                  body.get("payment_reference", ""))
        else:
            return JSONResponse(status_code=400,
                                content={"error": "nothing to change"})
    except _shop.CommerceError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"success": True, "order": o}



@router.post("/api/{website_id}/price")
async def set_item_price(website_id: str, request: Request):
    """Set what the product costs. Turns an enquiry-only site into a shop."""
    if not merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    body = await request.json()
    try:
        cat = _shop.set_price(website_id, body.get("price", ""),
                              body.get("item_id", "p1"))
    except _shop.CommerceError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    item = (cat.get("items") or [{}])[0]
    return {"success": True, "orderable": cat.get("orderable"),
            "price_display": _shop.format_money(item.get("price_minor"),
                                                cat.get("currency", "INR"))}

@router.post("/api/{website_id}/settings")
async def shop_settings(website_id: str, request: Request):
    """Seller settings — UPI ID, payee name, where to send new-order alerts."""
    if not merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    body = await request.json()
    if body.get("upi_vpa"):
        try:
            _pay.normalise_vpa(body["upi_vpa"])
        except _pay.PaymentError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
    return {"success": True, "settings": _shop.save_settings(website_id, body)}



# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHING
#
# Doc §3: the platform handles hosting so the seller never touches a server.
# The `local` target needs no account and no token, so a site can be live the
# moment it is generated.
# ══════════════════════════════════════════════════════════════════════════════
