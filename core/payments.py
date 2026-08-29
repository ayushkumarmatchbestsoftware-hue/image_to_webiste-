"""
Payments — cash on delivery and UPI.

Two methods, chosen because they need no merchant account, no gateway
onboarding and no API keys: a vendor can take money the day their site is
generated. Card gateways come later behind the same interface.

The honest part, which shapes the whole design:

  A UPI deep link CANNOT confirm that money arrived.

It opens the buyer's payment app with the amount and reference filled in, and
that is all it does. Nothing comes back. Any code that marks an order paid
because the buyer tapped the button is lying to the seller, and the seller
ships goods for free.

So payment here is a three-step claim, never an assertion:

  unpaid    order placed, nothing has happened
  pending   the buyer says they paid and gave a UTR reference
  paid      the SELLER checked their bank and confirmed it

Only a human — or, later, a real gateway webhook — moves an order to `paid`.
That is not a limitation to work around; it is what makes the record true.

This module never sees a card number. When a hosted gateway is added it will
be a redirect to the provider's own page, for the same reason.
"""
import logging
import os
import re
import urllib.parse

logger = logging.getLogger("payments")

# A UPI VPA: handle@bank. Deliberately strict — a typo here means a buyer's
# money goes to a stranger, and there is no getting it back.
VPA_RE = re.compile(r"^[a-zA-Z0-9.\-_]{2,64}@[a-zA-Z][a-zA-Z0-9.\-]{1,32}$")


class PaymentError(Exception):
    """A payment configuration or request that cannot be honoured."""


def normalise_vpa(vpa: str) -> str:
    """Validate a seller's UPI ID, or raise. Never guessed at or 'corrected'."""
    v = str(vpa or "").strip()
    if not v:
        raise PaymentError("no UPI ID set")
    if not VPA_RE.match(v):
        raise PaymentError(
            "that does not look like a UPI ID — it should read like name@bank")
    return v


def methods_for(settings: dict) -> list:
    """
    Which methods this seller can actually offer, given what they configured.

    A method is only listed when it can complete. Offering UPI with no VPA set
    produces a buyer stuck on a dead button, which loses the sale and the trust.
    """
    out = [{
        "id": "cod",
        "label": "Cash on delivery",
        "blurb": "Pay the seller when your order arrives.",
        "needs_reference": False,
    }]
    vpa = (settings or {}).get("upi_vpa", "")
    if vpa:
        try:
            normalise_vpa(vpa)
        except PaymentError as e:
            logger.warning(f"UPI not offered: {e}")
            return out
        out.append({
            "id": "upi",
            "label": "UPI",
            "blurb": "Pay now with any UPI app, then enter the reference.",
            "needs_reference": True,
        })
    return out


def upi_link(settings: dict, order: dict) -> dict:
    """
    Build the UPI intent for an order.

    Returns the deep link, the VPA in plain text (so a desktop buyer can copy it
    into their own app), and the exact amount. The order's own id travels as the
    transaction reference so the seller can match a bank line to an order
    without asking the buyer.
    """
    vpa = normalise_vpa((settings or {}).get("upi_vpa", ""))
    payee = str((settings or {}).get("upi_name") or
                (settings or {}).get("site_name") or "Seller").strip()[:60]

    if order.get("currency") != "INR":
        raise PaymentError("UPI settles in INR only")
    minor = int(order.get("total_minor") or 0)
    if minor <= 0:
        raise PaymentError("nothing to pay")
    # UPI wants rupees with two decimals, not paise.
    amount = f"{minor // 100}.{minor % 100:02d}"

    params = {
        "pa": vpa,
        "pn": payee,
        "am": amount,
        "cu": "INR",
        "tn": f"Order {order.get('id', '')}"[:50],
        "tr": str(order.get("id", ""))[:35],
    }
    link = "upi://pay?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return {"method": "upi", "link": link, "vpa": vpa,
            "payee": payee, "amount": amount, "currency": "INR",
            "reference": params["tr"]}


def instructions(settings: dict, order: dict) -> dict:
    """
    What to show the buyer once the order exists, for whichever method they
    chose. Shaped for the page so the template stays free of payment logic.
    """
    method = order.get("payment_method", "cod")
    if method == "cod":
        return {
            "method": "cod",
            "headline": "Order placed",
            "body": "Pay the seller in cash when your order arrives. "
                    "They will contact you to confirm delivery.",
            "needs_reference": False,
        }
    if method == "upi":
        pay = upi_link(settings, order)
        return {
            "method": "upi",
            "headline": "Pay to complete your order",
            "body": f"Send {pay['amount']} to {pay['vpa']} using any UPI app, "
                    f"then enter the reference number below so the seller can "
                    f"match your payment.",
            "needs_reference": True,
            **pay,
        }
    raise PaymentError(f"unknown payment method {method!r}")


# A UPI UTR is 12 digits. Other apps show a shorter reference, so this stays
# permissive about format while refusing something that is clearly not a
# reference at all — an empty box or a word.
def clean_reference(text: str) -> str:
    ref = re.sub(r"\s+", "", str(text or ""))
    if not re.fullmatch(r"[A-Za-z0-9\-]{6,32}", ref):
        raise PaymentError(
            "enter the reference or UTR number from your payment app")
    return ref
