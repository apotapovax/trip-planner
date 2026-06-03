"""Travel preference tools for FlightClaw — backend (D1) backed.

Preferences are entered once and drive recommend_flights ranking. They also
accumulate 'learnings' over time from post-trip feedback (the learning loop).
"""

import json

import profile_api

_SCALAR_FIELDS = [
    "preferred_cabin", "alliance", "seat", "depart_window", "max_stops",
    "redeye_ok", "baggage", "meal", "budget_sensitivity",
]


def _fmt_preferences(prefs):
    if not prefs:
        return "No preferences set yet. Use set_preferences."
    lines = ["Travel preferences:"]
    if prefs.get("cabin_rules"):
        cr = prefs["cabin_rules"]
        lines.append(f"  Cabin: shorthaul={cr.get('shorthaul', '?')}, longhaul={cr.get('longhaul', '?')}")
    elif prefs.get("preferred_cabin"):
        lines.append(f"  Cabin: {prefs['preferred_cabin']}")
    for f in ["preferred_airlines", "avoid_airlines"]:
        if prefs.get(f):
            lines.append(f"  {f.replace('_', ' ').title()}: {', '.join(prefs[f])}")
    for f in ["alliance", "seat", "depart_window", "max_stops", "baggage", "meal", "budget_sensitivity"]:
        if prefs.get(f) is not None:
            lines.append(f"  {f.replace('_', ' ').title()}: {prefs[f]}")
    if "redeye_ok" in prefs:
        lines.append(f"  Red-eye OK: {prefs['redeye_ok']}")
    if prefs.get("notes"):
        lines.append("  Notes:")
        lines.extend(f"    - {n}" for n in prefs["notes"])
    if prefs.get("learnings"):
        lines.append("  Learnings (from past trips):")
        lines.extend(f"    - {n}" for n in prefs["learnings"])
    return "\n".join(lines)


def register_preferences_tools(mcp):
    """Register travel-preference tools on the MCP server."""

    @mcp.tool()
    def set_preferences(
        shorthaul_cabin: str | None = None,
        longhaul_cabin: str | None = None,
        preferred_airlines: str | None = None,
        avoid_airlines: str | None = None,
        alliance: str | None = None,
        seat: str | None = None,
        depart_window: str | None = None,
        max_stops: str | None = None,
        redeye_ok: bool | None = None,
        baggage: str | None = None,
        meal: str | None = None,
        budget_sensitivity: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Set the user's travel preferences (one-time onboarding; patch any subset later).

        Only provided fields are updated. Notes are appended (kept as history).

        Args:
            shorthaul_cabin: Cabin for short-haul flights (ECONOMY/PREMIUM_ECONOMY/BUSINESS/FIRST)
            longhaul_cabin: Cabin for long-haul flights (e.g. BUSINESS)
            preferred_airlines: Comma-separated IATA codes preferred (e.g. BA,AA)
            avoid_airlines: Comma-separated IATA codes to avoid (e.g. NK,F9)
            alliance: Preferred alliance (oneworld, staralliance, skyteam)
            seat: Seat preference (aisle, window, no preference)
            depart_window: Preferred departure window 'HH-HH' (e.g. '8-20') or word (morning/afternoon/evening)
            max_stops: Max stops tolerated (NON_STOP, ONE_STOP, ANY)
            redeye_ok: Whether overnight/red-eye flights are acceptable
            baggage: Baggage preference (e.g. 'carry-on only', 'one checked bag')
            meal: Meal preference (e.g. 'vegetarian')
            budget_sensitivity: cheapest | balanced | comfort (drives ranking weight)
            notes: A free-form preference note to append
        """
        patch = {}
        cabin_rules = {}
        if shorthaul_cabin:
            cabin_rules["shorthaul"] = shorthaul_cabin.upper()
        if longhaul_cabin:
            cabin_rules["longhaul"] = longhaul_cabin.upper()
        if cabin_rules:
            patch["cabin_rules"] = cabin_rules
        if preferred_airlines is not None:
            patch["preferred_airlines"] = [a.strip().upper() for a in preferred_airlines.split(",") if a.strip()]
        if avoid_airlines is not None:
            patch["avoid_airlines"] = [a.strip().upper() for a in avoid_airlines.split(",") if a.strip()]
        if alliance is not None:
            patch["alliance"] = alliance.lower().strip()
        if seat is not None:
            patch["seat"] = seat.lower().strip()
        if depart_window is not None:
            patch["depart_window"] = depart_window.strip()
        if max_stops is not None:
            patch["max_stops"] = max_stops.upper().strip()
        if redeye_ok is not None:
            patch["redeye_ok"] = redeye_ok
        if baggage is not None:
            patch["baggage"] = baggage
        if meal is not None:
            patch["meal"] = meal
        if budget_sensitivity is not None:
            patch["budget_sensitivity"] = budget_sensitivity.lower().strip()
        if notes:
            patch["notes"] = [notes]

        if not patch:
            return "No preferences provided."
        try:
            result = profile_api.patch_preferences(patch)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        return "Preferences updated.\n\n" + _fmt_preferences(result)

    @mcp.tool()
    def get_preferences() -> str:
        """Show the user's saved travel preferences and accumulated learnings."""
        try:
            prefs = profile_api.get_preferences()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        return _fmt_preferences(prefs)

    @mcp.tool()
    def update_preferences(patch_json: str) -> str:
        """Patch preferences with a raw JSON object (advanced; merges into existing).

        'notes' and 'learnings' arrays are appended; other keys overwrite.

        Args:
            patch_json: JSON object, e.g. {"seat":"window","preferred_airlines":["BA"]}
        """
        try:
            patch = json.loads(patch_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"
        if not isinstance(patch, dict):
            return "patch_json must be a JSON object."
        try:
            result = profile_api.patch_preferences(patch)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        return "Preferences updated.\n\n" + _fmt_preferences(result)
