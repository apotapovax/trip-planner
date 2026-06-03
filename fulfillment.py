"""Booking fulfillment beyond Duffel.

Extends bookable coverage with Kiwi.com (Tequila API) when a key is configured,
and always offers an affiliate / deep-link hand-off as the universal fallback so
no recommended flight is ever a dead end.

Env:
  KIWI_API_KEY   Tequila API key — enables the Kiwi bookability check (search).
  KIWI_AFFILID   Kiwi affiliate id — used for monetised hand-off deep links.
Note: Kiwi *search* works with an API key; actually *booking* via Kiwi's API
needs a commercial deposit account. Until then, Kiwi-covered flights are handed
off via deep link.
"""

import json
import os
import urllib.parse
import urllib.request

GOOGLE_BOOKING_URL = "https://www.google.com/travel/flights/booking?tfs="
TEQUILA_SEARCH = "https://api.tequila.kiwi.com/v2/search"


def _digits(n):
    return "".join(c for c in str(n) if c.isdigit())


def _kiwi_date(d):
    """YYYY-MM-DD -> dd/mm/yyyy (Tequila format)."""
    try:
        y, m, day = d.split("-")
        return f"{day}/{m}/{y}"
    except ValueError:
        return d


def kiwi_configured():
    return bool(os.environ.get("KIWI_API_KEY"))


def kiwi_index(origin, destination, date, return_date=None):
    """Look up what Kiwi can book on this route.

    Returns {segments:set[(carrier,fnum)], deep_link:str|None} or None if Kiwi
    isn't configured or the search fails.
    """
    key = os.environ.get("KIWI_API_KEY")
    if not key:
        return None
    params = {
        "fly_from": origin,
        "fly_to": destination,
        "date_from": _kiwi_date(date),
        "date_to": _kiwi_date(date),
        "curr": "GBP",
        "limit": 200,
        "vehicle_type": "aircraft",
    }
    if return_date:
        params["return_from"] = _kiwi_date(return_date)
        params["return_to"] = _kiwi_date(return_date)
    url = f"{TEQUILA_SEARCH}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"apikey": key, "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    segments = set()
    itineraries = data.get("data", []) or []
    for it in itineraries:
        for leg in it.get("route", []):
            c = leg.get("airline")
            fn = _digits(leg.get("flight_no", ""))
            if c and fn:
                segments.add((c, fn))
    deep = itineraries[0].get("deep_link") if itineraries else None
    return {"segments": segments, "deep_link": deep}


def book_direct_link(origin, destination, date, google_token=None):
    """A tappable hand-off link for flights we can't book in-app.

    Prefers a monetised Kiwi affiliate deep link, then the precise Google Flights
    booking link, then a generic Skyscanner search.
    """
    affil = os.environ.get("KIWI_AFFILID")
    if affil:
        q = urllib.parse.urlencode({
            "from": origin, "to": destination,
            "departure": date, "affilid": affil,
        })
        return f"https://www.kiwi.com/deep?{q}"
    if google_token:
        return GOOGLE_BOOKING_URL + urllib.parse.quote(google_token, safe="")
    ymd = date.replace("-", "")[2:]  # yymmdd
    return f"https://www.skyscanner.net/transport/flights/{origin}/{destination}/{ymd}/"
