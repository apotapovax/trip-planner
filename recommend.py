"""Preference-aware flight recommendation for FlightClaw.

Pulls the user's saved travel preferences from the backend, runs the existing
Google Flights search, then scores and ranks results against those preferences
and returns the few options that best fit *this* user — with reasons.
"""

import urllib.parse
from datetime import datetime

import duffel_api
import fulfillment
import profile_api
from helpers import build_filters, format_duration, format_flight
from search_utils import fmt_price, search_with_currency

BOOKING_BASE_URL = "https://www.google.com/travel/flights/booking?tfs="

# Budget sensitivity → (price, duration, stops) score weights.
_WEIGHTS = {
    "cheapest": (0.7, 0.2, 0.1),
    "balanced": (0.45, 0.3, 0.25),
    "comfort": (0.2, 0.4, 0.4),
}
_LONGHAUL_MINUTES = 360  # 6h+ counts as long-haul for cabin rules


def _parse_window(window):
    """Return (earliest_hour, latest_hour) from 'HH-HH' or a word, else (None, None)."""
    if not window:
        return None, None
    words = {"morning": (5, 12), "afternoon": (12, 18), "evening": (17, 23), "night": (20, 23)}
    w = window.lower().strip()
    if w in words:
        return words[w]
    if "-" in w:
        try:
            a, b = w.split("-", 1)
            return int(a), int(b)
        except ValueError:
            return None, None
    return None, None


def _flatten(result):
    """Normalize a search result (one-way flight or (outbound, return) tuple)."""
    if isinstance(result, tuple) and len(result) == 2 and hasattr(result[0], "price"):
        out, ret = result
        flights = [out, ret]
    else:
        flights = [result[0] if isinstance(result, tuple) else result]
    # Skip results missing price/duration (some Google Flights rows are partial).
    if any(f.price is None or f.duration is None for f in flights):
        return None
    price = sum(f.price for f in flights)
    duration = sum(f.duration for f in flights)
    stops = sum((f.stops or 0) for f in flights)
    legs = [leg for f in flights for leg in f.legs]
    return {"flights": flights, "price": price, "duration": duration, "stops": stops, "legs": legs}


def _airline_codes(legs):
    return {getattr(leg.airline, "name", "") for leg in legs}


def _is_redeye(legs):
    for leg in legs:
        dep = leg.departure_datetime
        arr = leg.arrival_datetime
        # Overnight if it crosses midnight or departs late and arrives early.
        if arr.date() > dep.date():
            return True
        if dep.hour >= 22 or arr.hour <= 6:
            return True
    return False


def _resolve_cabin(prefs, cabin_arg, origin, destination, date, return_date, base_kwargs):
    """Pick cabin: explicit arg > preferred_cabin > cabin_rules (probe haul) > ECONOMY."""
    if cabin_arg:
        return cabin_arg.upper(), None
    if prefs.get("preferred_cabin"):
        return prefs["preferred_cabin"].upper(), None
    rules = prefs.get("cabin_rules")
    if not rules:
        return "ECONOMY", None
    # Probe in economy to estimate haul length.
    try:
        filters = build_filters(origin, destination, date, return_date, "ECONOMY", **base_kwargs)
        probe, _cur = search_with_currency(filters, top_n=1)
    except Exception:
        probe = None
    haul = "shorthaul"
    if probe:
        info = _flatten(probe[0][0])
        if info and info["duration"] >= _LONGHAUL_MINUTES:
            haul = "longhaul"
    cabin = rules.get(haul) or rules.get("shorthaul") or rules.get("longhaul") or "ECONOMY"
    return cabin.upper(), haul


def _norm_fnum(n):
    """Normalize a flight number to digits only for cross-source comparison."""
    return "".join(c for c in str(n) if c.isdigit())


def _duffel_index(origin, destination, date, return_date, cabin, adults, children, infants):
    """One Duffel lookup for the same route → what's actually bookable there.

    Returns {segments:set[(carrier,fnum)], owners:dict[code]->(price,offer_id,currency),
    test_mode:bool} or None if Duffel isn't configured / the search fails.
    """
    if not duffel_api.is_configured():
        return None
    try:
        data = duffel_api.search(
            origin, destination, date, return_date, cabin,
            adults, children, infants, 1,
        )
    except Exception:
        return None
    offers = data.get("offers", [])
    segments = set()
    owners = {}
    test_mode = False
    for o in offers:
        owner = o.get("owner", {})
        code = owner.get("iata_code")
        if owner.get("name") == "Duffel Airways" or code == "ZZ":
            test_mode = True
        try:
            price = float(o.get("total_amount", "0"))
        except (TypeError, ValueError):
            price = 0.0
        cur = o.get("total_currency", "")
        if code and (code not in owners or price < owners[code][0]):
            owners[code] = (price, o.get("id"), cur)
        for s in o.get("slices", []):
            for seg in s.get("segments", []):
                mc = seg.get("marketing_carrier", {})
                c = mc.get("iata_code")
                fn = _norm_fnum(seg.get("marketing_carrier_flight_number", ""))
                if c and fn:
                    segments.add((c, fn))
    return {"segments": segments, "owners": owners, "test_mode": test_mode}


def _bookability(option, dindex, kindex, origin, destination, date):
    """Route one fli option to the best fulfilment path. Returns (kind, detail).

    Precedence: exact flight on Duffel (in-app book+pay) > exact flight on Kiwi >
    same airline available on Duffel > deep-link hand-off (never a dead end).
    """
    option_segs = [
        (leg.airline.name, _norm_fnum(leg.flight_number))
        for f in option["flights"] for leg in f.legs
    ]
    primary = option["flights"][0].legs[0].airline.name

    if dindex and option_segs and all(seg in dindex["segments"] for seg in option_segs):
        own = dindex["owners"].get(primary)
        ptr = f" (Duffel {own[2]} {own[0]:.0f}, offer {own[1]})" if own else ""
        return "duffel", f"✅ bookable in-app via Duffel{ptr}"

    if kindex and option_segs and all(seg in kindex["segments"] for seg in option_segs):
        return "kiwi", "✅ bookable via Kiwi (LCC/OTA coverage)"

    if dindex and primary in dindex["owners"]:
        own = dindex["owners"][primary]
        return "same_airline", (
            f"≈ {primary} on Duffel, different flight/time (from {own[2]} {own[0]:.0f}) "
            f"— use duffel_search_flights"
        )

    link = fulfillment.book_direct_link(origin, destination, date, option.get("token"))
    return "direct", f"↗ {primary} not in-app — book direct: {link}"


def register_recommend_tools(mcp):
    """Register the preference-aware recommendation tool."""

    @mcp.tool()
    def recommend_flights(
        origin: str,
        destination: str,
        date: str,
        return_date: str | None = None,
        cabin: str | None = None,
        adults: int = 1,
        children: int = 0,
        infants_in_seat: int = 0,
        infants_on_lap: int = 0,
        candidates: int = 12,
    ) -> str:
        """Recommend the best flights for THIS user by ranking search results against saved preferences.

        Loads the user's travel preferences (set via set_preferences) and uses them to
        choose cabin, filter out avoided airlines, and score each option on price,
        duration, stops, preferred airlines, departure window and red-eye tolerance.
        Returns the top 3 with a short "why this fits you" for each.

        Args:
            origin: Origin IATA code (e.g. LHR)
            destination: Destination IATA code (e.g. JFK)
            date: Departure date (YYYY-MM-DD)
            return_date: Return date for round trips (YYYY-MM-DD)
            cabin: Override cabin (else taken from preferences). ECONOMY/PREMIUM_ECONOMY/BUSINESS/FIRST
            adults: Adults (default 1)
            children: Children (default 0)
            infants_in_seat: Infants in seat (default 0)
            infants_on_lap: Infants on lap (default 0)
            candidates: How many raw results to consider before ranking (default 12)
        """
        origin = origin.strip().upper()
        destination = destination.strip().upper()

        prefs = {}
        prefs_note = ""
        if profile_api.is_configured():
            try:
                prefs = profile_api.get_preferences() or {}
            except profile_api.ProfileError as e:
                prefs_note = f"(preferences unavailable: {e})"
        else:
            prefs_note = "(backend not configured — ranking on price/duration/stops only)"

        # Preference-derived search params.
        max_stops_pref = prefs.get("max_stops")
        earliest, latest = _parse_window(prefs.get("depart_window"))
        base_kwargs = dict(
            adults=adults, children=children,
            infants_in_seat=infants_in_seat, infants_on_lap=infants_on_lap,
            stops=max_stops_pref or "ANY",
            earliest_departure=earliest, latest_departure=latest,
        )

        resolved_cabin, haul = _resolve_cabin(
            prefs, cabin, origin, destination, date, return_date, base_kwargs
        )

        try:
            filters = build_filters(
                origin, destination, date, return_date, resolved_cabin, **base_kwargs
            )
        except Exception as e:
            return f"Could not build search: {e}"

        results, currency = search_with_currency(filters, top_n=candidates)
        if not results:
            return f"No flights found for {origin} -> {destination} on {date}."

        avoid = {a.upper() for a in prefs.get("avoid_airlines", [])}
        preferred = {a.upper() for a in prefs.get("preferred_airlines", [])}
        weights = _WEIGHTS.get((prefs.get("budget_sensitivity") or "balanced").lower(), _WEIGHTS["balanced"])
        redeye_ok = prefs.get("redeye_ok", True)

        scored, dropped = [], 0
        for result, token in results:
            info = _flatten(result)
            if not info:
                continue
            codes = _airline_codes(info["legs"])
            if avoid and codes & avoid:
                dropped += 1
                continue
            info["codes"] = codes
            info["token"] = token
            scored.append(info)

        if not scored:
            return (
                f"All {len(results)} options were operated by airlines you avoid "
                f"({', '.join(sorted(avoid))}). Loosen avoid_airlines to see them."
            )

        prices = [s["price"] for s in scored]
        durs = [s["duration"] for s in scored]
        stops = [s["stops"] for s in scored]
        p_lo, p_hi = min(prices), max(prices)
        d_lo, d_hi = min(durs), max(durs)
        s_lo, s_hi = min(stops), max(stops)

        def norm(v, lo, hi):
            return 0.0 if hi == lo else (v - lo) / (hi - lo)

        wp, wd, ws = weights
        for s in scored:
            score = (
                wp * (1 - norm(s["price"], p_lo, p_hi))
                + wd * (1 - norm(s["duration"], d_lo, d_hi))
                + ws * (1 - norm(s["stops"], s_lo, s_hi))
            )
            reasons = []
            if s["price"] == p_lo:
                reasons.append("cheapest option")
            if s["duration"] == d_lo:
                reasons.append("fastest")
            if s["stops"] == s_lo and s_lo == 0:
                reasons.append("non-stop")
            if preferred and s["codes"] & preferred:
                score += 0.15
                reasons.append(f"on preferred airline ({', '.join(sorted(s['codes'] & preferred))})")
            dep_hour = s["legs"][0].departure_datetime.hour
            if earliest is not None and latest is not None and earliest <= dep_hour <= latest:
                score += 0.1
                reasons.append("departs in your preferred window")
            if not redeye_ok and _is_redeye(s["legs"]):
                score -= 0.15
                reasons.append("red-eye (you usually avoid)")
            s["score"] = score
            s["reasons"] = reasons

        scored.sort(key=lambda s: s["score"], reverse=True)
        top = scored[:3]

        # Reconcile the picks against what's actually bookable: Duffel (in-app)
        # first, then Kiwi (LCC/OTA coverage), then a deep-link hand-off.
        dindex = _duffel_index(
            origin, destination, date, return_date, resolved_cabin,
            adults, children, infants_in_seat + infants_on_lap,
        )
        kindex = fulfillment.kiwi_index(origin, destination, date, return_date)

        header = f"Recommended for you: {origin} -> {destination} on {date}"
        if return_date:
            header += f" (return {return_date})"
        header += f" — {resolved_cabin}"
        if haul:
            header += f" [{haul} per your cabin rule]"
        lines = [header]
        if prefs_note:
            lines.append(prefs_note)
        lines.append("")

        labels = ["Best for you", "Runner-up", "Also worth it"]
        for i, s in enumerate(top):
            label = labels[i] if i < len(labels) else f"Option {i+1}"
            lines.append(f"{label}: {fmt_price(s['price'], currency)} | "
                         f"{format_duration(s['duration'])} | {s['stops']} stop(s)")
            for f in s["flights"]:
                for leg in f.legs:
                    lines.append(
                        f"    {leg.airline.name} {leg.flight_number}: "
                        f"{leg.departure_airport.name} {leg.departure_datetime.strftime('%H:%M')} -> "
                        f"{leg.arrival_airport.name} {leg.arrival_datetime.strftime('%H:%M')}"
                    )
            if s["reasons"]:
                lines.append(f"    Why: {'; '.join(s['reasons'])}")
            _kind, detail = _bookability(s, dindex, kindex, origin, destination, date)
            if detail:
                lines.append(f"    {detail}")
            lines.append("")

        if dropped:
            lines.append(f"({dropped} option(s) hidden — operated by airlines you avoid.)")
        if dindex and dindex.get("test_mode"):
            lines.append("⚠️ Duffel is in TEST/sandbox mode — bookings won't be real until a live token is set.")
        if dindex is None:
            lines.append("(Duffel bookability check skipped — Duffel not configured.)")
        if not fulfillment.kiwi_configured():
            lines.append("(Kiwi coverage off — set KIWI_API_KEY to extend bookable airlines.)")
        lines.append(
            "To book a ✅ Duffel option, use duffel_search_flights then duffel_book_with_link."
        )
        return "\n".join(lines)
