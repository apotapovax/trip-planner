"""Thin wrapper around the link-cli for agent-billing payment flows."""

import json
import subprocess


class LinkError(RuntimeError):
    pass


def _run(args, timeout=600):
    """Run link-cli with --format json and return parsed output."""
    cmd = ["link-cli", *args, "--format", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise LinkError("link-cli not installed. Install via `brew install link-cli`.")
    except subprocess.TimeoutExpired:
        raise LinkError(f"link-cli timed out after {timeout}s.")

    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise LinkError(f"link-cli failed: {msg}")

    out = proc.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def is_authenticated():
    try:
        result = _run(["auth", "status"], timeout=10)
    except LinkError:
        return False
    if isinstance(result, list) and result:
        return bool(result[0].get("authenticated"))
    if isinstance(result, dict):
        return bool(result.get("authenticated"))
    return False


def list_payment_methods():
    result = _run(["payment-methods", "list"], timeout=30)
    return result if isinstance(result, list) else []


def first_payment_method_id():
    methods = list_payment_methods()
    if not methods:
        raise LinkError(
            "No payment methods configured. Run `link-cli payment-methods add`."
        )
    return methods[0].get("id")


def create_card_spend_request(
    *,
    payment_method_id,
    amount_cents,
    currency,
    merchant_name,
    merchant_url,
    context,
    line_items=None,
    total=None,
    test=False,
):
    """Create a card-credential spend request and poll until approved/denied/expired.

    Returns the spend request dict (status will be one of approved/denied/expired/etc).
    """
    if amount_cents <= 0:
        raise LinkError("Amount must be > 0 cents.")
    if amount_cents > 50000:
        raise LinkError(
            f"Amount {amount_cents}c exceeds Link's $500 spend-request limit."
        )
    if len(context) < 100:
        raise LinkError("Context must be at least 100 characters.")

    args = [
        "spend-request", "create",
        "--credential-type", "card",
        "--payment-method-id", payment_method_id,
        "--amount", str(amount_cents),
        "--currency", currency.lower(),
        "--merchant-name", merchant_name,
        "--merchant-url", merchant_url,
        "--context", context,
        "--request-approval",
    ]
    for li in line_items or []:
        args += ["--line-item", li]
    for t in total or []:
        args += ["--total", t]
    if test:
        args.append("--test")

    return _run(args, timeout=600)


def retrieve_with_card(spend_request_id):
    """Retrieve a spend request including the card credential."""
    return _run(
        ["spend-request", "retrieve", spend_request_id, "--include", "card"],
        timeout=30,
    )


def extract_card(spend_request):
    """Pull a card credential out of a spend-request payload, if present."""
    if not isinstance(spend_request, dict):
        return None
    card = spend_request.get("card") or spend_request.get("credential", {}).get("card")
    if not card:
        return None
    return {
        "number": card.get("number"),
        "exp_month": card.get("exp_month") or card.get("expMonth"),
        "exp_year": card.get("exp_year") or card.get("expYear"),
        "cvc": card.get("cvc") or card.get("cvv"),
        "holder_name": card.get("holder_name") or card.get("cardholder_name"),
        "brand": card.get("brand"),
        "last4": card.get("last4"),
    }
