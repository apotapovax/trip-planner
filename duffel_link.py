"""Duffel + Link agent-billing tools — pay for Duffel bookings via a Link virtual card."""

import json
import os

import duffel_api
import link_payment
from duffel_fmt import fmt_offer, resolve_passengers


def _amount_to_cents(amount_str, currency):
    """Convert a Duffel amount string ('234.50') + currency to integer minor units."""
    zero_decimal = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "RWF", "PYG"}
    amount = float(amount_str)
    if currency.upper() in zero_decimal:
        return int(round(amount))
    return int(round(amount * 100))


def _build_context(offer, passengers):
    """Produce the >=100-char context string the user sees in the approval dialog."""
    pax_count = len(passengers) if passengers else 1
    legs = []
    for s in offer.get("slices", []):
        orig = s.get("origin", {}).get("iata_code", "?")
        dest = s.get("destination", {}).get("iata_code", "?")
        segs = s.get("segments", [])
        dep = segs[0].get("departing_at", "")[:10] if segs else ""
        legs.append(f"{orig}->{dest} {dep}".strip())
    route = "; ".join(legs) or "flight booking"
    owner = offer.get("owner", {}).get("name", "airline")
    total = f"{offer.get('total_currency')} {offer.get('total_amount')}"
    ctx = (
        f"Flight booking via Duffel on {owner}. "
        f"Route: {route}. Passengers: {pax_count}. Total: {total}. "
        f"Payment will be charged to the airline/OTA at checkout."
    )
    while len(ctx) < 100:
        ctx += " Booked through FlightClaw."
    return ctx


def _build_line_items(offer):
    items = []
    base = offer.get("base_amount")
    tax = offer.get("tax_amount")
    currency = offer.get("total_currency", "")
    if base:
        items.append(f"description:Base fare,amount:{_amount_to_cents(base, currency)}")
    if tax:
        items.append(f"description:Taxes & fees,amount:{_amount_to_cents(tax, currency)}")
    return items


def register_duffel_link_tools(mcp):
    """Register the Duffel + Link agent-billing tools on the MCP server."""

    @mcp.tool()
    def link_list_payment_methods() -> str:
        """List Link payment methods on the user's account. Use to pick a payment_method_id."""
        try:
            methods = link_payment.list_payment_methods()
        except link_payment.LinkError as e:
            return f"Link error: {e}"
        if not methods:
            return "No payment methods. Run `link-cli payment-methods add` to add one."
        lines = []
        for m in methods:
            brand = m.get("brand") or m.get("card_brand") or ""
            last4 = m.get("last4") or m.get("card_last4") or ""
            label = f"{brand} ****{last4}".strip()
            lines.append(f"  {m.get('id', '?')}  {label}")
        return "\n".join(lines)

    @mcp.tool()
    def duffel_book_with_link(
        offer_id: str,
        passengers: str,
        payment_method_id: str | None = None,
        services: str | None = None,
        test: bool = False,
    ) -> str:
        """Pay for a Duffel offer with a Link virtual card. Returns card details + Duffel checkout URL.

        Flow:
          1. Fetches the offer to get amount/currency/summary.
          2. Creates a Duffel hosted checkout page (server-side).
          3. Creates a Link spend request for the offer amount and waits for user approval.
          4. On approval, returns the virtual card credential and the checkout URL.
          5. The agent then uses Chrome browser automation to enter the card on the checkout page.

        Args:
            offer_id: Duffel offer ID (from duffel_search_flights).
            passengers: Comma-separated profile names ("jack,jane") or JSON passenger array.
            payment_method_id: Link payment method ID. Omit to use the first one on the account.
            services: Optional JSON array of extras, e.g. [{"id":"ase_xxx","quantity":1}].
            test: If True, creates a Link testmode credential.
        """
        if not link_payment.is_authenticated():
            return "Not signed in to link-cli. Run `link-cli auth login` first."

        pax, err = resolve_passengers(passengers)
        if err:
            return err

        try:
            offer = duffel_api.get_offer(offer_id)
        except Exception as e:
            return f"Could not fetch offer: {e}"

        amount_str = offer.get("total_amount")
        currency = offer.get("total_currency")
        if not amount_str or not currency:
            return "Offer is missing total_amount/total_currency."

        if pax and not pax[0].get("id"):
            offer_pax = offer.get("passengers", [])
            for i, p in enumerate(pax):
                if i < len(offer_pax):
                    p["id"] = offer_pax[i]["id"]

        svc_list = None
        if services:
            try:
                svc_list = json.loads(services)
            except json.JSONDecodeError as e:
                return f"Invalid services JSON: {e}"

        summary = fmt_offer(offer)

        try:
            checkout = duffel_api.create_checkout(
                offer_id, pax, amount_str, currency, summary, svc_list,
            )
        except Exception as e:
            return f"Checkout creation failed: {e}"

        base = os.environ.get("FLIGHTCLAW_API_URL", "").rstrip("/")
        checkout_url = f"{base}{checkout.get('checkout_url', '')}"
        fee = checkout.get("fee", 0)

        amount_cents = _amount_to_cents(amount_str, currency)
        if amount_cents > 50000:
            return (
                f"Offer total {currency} {amount_str} ({amount_cents}c) exceeds Link's "
                f"$500 spend-request limit. Use duffel_book_flight (Duffel balance) instead."
            )

        try:
            pm_id = payment_method_id or link_payment.first_payment_method_id()
        except link_payment.LinkError as e:
            return f"Link error: {e}"

        context = _build_context(offer, pax)
        line_items = _build_line_items(offer)
        total = [f"description:Total,amount:{amount_cents}"]

        try:
            spend_request = link_payment.create_card_spend_request(
                payment_method_id=pm_id,
                amount_cents=amount_cents,
                currency=currency,
                merchant_name=offer.get("owner", {}).get("name") or "Duffel",
                merchant_url=checkout_url,
                context=context,
                line_items=line_items,
                total=total,
                test=test,
            )
        except link_payment.LinkError as e:
            return f"Spend request failed: {e}"

        sr = spend_request[0] if isinstance(spend_request, list) else spend_request
        status = (sr or {}).get("status", "unknown")
        sr_id = (sr or {}).get("id", "")

        if status != "approved":
            return f"Spend request {sr_id} ended with status: {status}."

        try:
            full = link_payment.retrieve_with_card(sr_id)
        except link_payment.LinkError as e:
            return f"Could not retrieve card: {e}"

        full = full[0] if isinstance(full, list) else full
        card = link_payment.extract_card(full or {})
        if not card or not card.get("number"):
            return f"Spend request {sr_id} approved but no card credential was returned."

        return (
            f"Link payment approved (spend_request={sr_id}).\n\n"
            f"Duffel checkout URL: {checkout_url}\n"
            f"Service fee included: {currency} {fee:.2f}\n\n"
            f"Virtual card to enter on the checkout page:\n"
            f"  Number: {card['number']}\n"
            f"  Expiry: {card['exp_month']:02d}/{card['exp_year']}\n"
            f"  CVC:    {card['cvc']}\n"
            f"  Holder: {card.get('holder_name') or '(use any name)'}\n\n"
            f"{summary}\n\n"
            f"Next step: use Chrome automation to navigate to the checkout URL and "
            f"submit these card details. Confirm with the user before submitting."
        )
