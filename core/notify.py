"""
Notifications — telling the seller an order arrived.

A shop whose owner has to keep a dashboard open is not a shop. This is the
piece that turns a stored order into something a person finds out about.

Three channels, chosen so the useful one needs no account:

  log       always on. The order shows in the server log.
  webhook   the seller pastes any URL — a Zapier / n8n / Make hook, their own
            endpoint — and gets a JSON POST. This is the one that reaches a
            phone without us signing up to anything, because those services
            already deliver to WhatsApp, Telegram, SMS and email for free.
  email     plain SMTP, if the seller has credentials to give.

WhatsApp is deliberately NOT here as a push channel. Sending a WhatsApp message
programmatically requires the Business API — a registered business, a template
approved in advance, and per-message billing. Pretending otherwise would mean a
button that silently does nothing. What the dashboard offers instead is a
wa.me link the seller taps to message the BUYER, which needs no API at all.

Everything here is best-effort and never raises into the caller. An order is
already safely on disk by the time this runs; failing to announce it must not
fail it.
"""
import json
import logging
import os
import threading
import urllib.error
import urllib.request

logger = logging.getLogger("notify")

TIMEOUT = float(os.getenv("NOTIFY_TIMEOUT", "6"))


def _money(order):
    from core.commerce import format_money
    return format_money(order.get("total_minor"), order.get("currency", "INR"))


def summarise(order: dict, site_name: str = "") -> str:
    """One-line human summary — the same text every channel sends."""
    c = order.get("customer", {}) or {}
    items = ", ".join(f"{l['qty']}x {l['name']}" for l in order.get("lines", []))
    who = c.get("name", "someone")
    contact = c.get("phone") or c.get("email") or ""
    return (f"New order {order.get('id')} on {site_name or 'your site'} — "
            f"{_money(order)} — {items} — from {who} {contact} — "
            f"paying by {order.get('payment_method', 'cod')}")


def payload(order: dict, site_name: str = "") -> dict:
    c = order.get("customer", {}) or {}
    return {
        "event": "order.created",
        "order_id": order.get("id"),
        "site": site_name,
        "website_id": order.get("website_id"),
        "total": _money(order),
        "total_minor": order.get("total_minor"),
        "currency": order.get("currency"),
        "payment_method": order.get("payment_method"),
        "payment_status": order.get("payment_status"),
        "items": [{"name": l["name"], "qty": l["qty"]} for l in order.get("lines", [])],
        "customer": {"name": c.get("name"), "phone": c.get("phone"),
                     "email": c.get("email"), "address": c.get("address"),
                     "note": c.get("note")},
        "text": summarise(order, site_name),
        "created_at": order.get("created_at"),
    }


def _post_webhook(url: str, body: dict) -> None:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "image-to-website/orders"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        logger.info(f"webhook {url.split('/')[2]} -> {r.status}")


def _send_email(to: str, subject: str, text: str) -> None:
    """
    Plain SMTP. Only runs when the seller has given credentials; there is no
    fallback sender, because sending on someone else's behalf from a shared
    address is how a domain gets blocklisted.
    """
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASS")
    if not (host and user and pwd and to):
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM", user)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=TIMEOUT) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
    logger.info(f"order email sent to {to}")


def order_placed(order: dict, settings: dict, site_name: str = "") -> None:
    """
    Announce a new order on every channel the seller configured.

    Runs on a background thread and swallows its own failures: the order is
    already durable, and a webhook that is down must not turn a successful
    checkout into an error for the buyer.
    """
    body = payload(order, site_name)
    logger.info(body["text"])

    def run():
        hook = (settings or {}).get("notify_webhook", "")
        if hook.startswith("https://") or hook.startswith("http://"):
            try:
                _post_webhook(hook, body)
            except (urllib.error.URLError, OSError, ValueError) as e:
                logger.warning(f"webhook failed: {e}")
        to = (settings or {}).get("notify_email", "")
        if to:
            try:
                _send_email(to, f"New order {order.get('id')} — {body['total']}",
                            body["text"])
            except Exception as e:
                logger.warning(f"order email failed: {e}")

    threading.Thread(target=run, daemon=True).start()


def whatsapp_link(phone: str, order: dict, site_name: str = "") -> str:
    """
    A wa.me link for the SELLER to message the buyer about their order.

    Not a push notification — it opens WhatsApp with the message ready. That
    needs no API, no business account and no approval, which is why it is the
    one WhatsApp feature offered here.
    """
    import urllib.parse
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not digits:
        return ""
    text = (f"Hello {order.get('customer', {}).get('name', '')}, "
            f"thanks for your order {order.get('id')} "
            f"({_money(order)}) from {site_name}. ")
    return f"https://wa.me/{digits}?text={urllib.parse.quote(text)}"
