"""Trip history & post-trip learning tools for FlightClaw — backend (D1) backed.

Log a trip after booking, surface trips due for follow-up, and record feedback
that distils into preference 'learnings' — this is the loop that makes FlightClaw
learn how the user likes to travel over time.
"""

from datetime import datetime, timedelta

import profile_api


def _slug(s):
    return "".join(c if c.isalnum() else "-" for c in (s or "").lower()).strip("-")


def _default_followup(depart_date, return_date):
    end = return_date or depart_date
    if not end:
        return None
    try:
        d = datetime.strptime(end, "%Y-%m-%d").date() + timedelta(days=1)
        return d.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _fmt_trip(t):
    line = f"  [{t['id']}] {t.get('route', '?')} {t.get('depart_date', '')}"
    if t.get("return_date"):
        line += f"–{t['return_date']}"
    line += f" | {t.get('status', '?')}"
    if t.get("travelers"):
        line += f" | {', '.join(t['travelers'])}"
    if t.get("price"):
        line += f" | {t.get('currency', '')} {t['price']}"
    if t.get("feedback"):
        line += f"\n      feedback: {t['feedback']}"
    return line


def register_trip_tools(mcp):
    """Register trip-history and follow-up tools on the MCP server."""

    @mcp.tool()
    def log_trip(
        route: str,
        depart_date: str,
        travelers: str,
        return_date: str | None = None,
        cabin: str | None = None,
        price: str | None = None,
        currency: str | None = None,
        order_id: str | None = None,
        id: str | None = None,
        status: str = "booked",
    ) -> str:
        """Record a booked trip in history (call after a successful booking).

        Args:
            route: Route summary, e.g. 'LHR->JFK' or 'LHR->JFK->LHR'
            depart_date: Outbound date (YYYY-MM-DD)
            travelers: Comma-separated traveler name keys (e.g. 'jack,jane')
            return_date: Return date for round trips (YYYY-MM-DD)
            cabin: Cabin booked (e.g. BUSINESS)
            price: Total price paid
            currency: Currency code (e.g. GBP)
            order_id: Duffel order ID if available (e.g. ord_xxx)
            id: Trip ID (optional; defaults to order_id or a route-date slug)
            status: booked | upcoming | completed | cancelled
        """
        traveler_list = [t.strip().lower() for t in travelers.split(",") if t.strip()]
        trip_id = id or order_id or f"{_slug(route)}-{depart_date}"
        try:
            result = profile_api.upsert_trip({
                "id": trip_id,
                "status": status,
                "order_id": order_id,
                "route": route,
                "depart_date": depart_date,
                "return_date": return_date,
                "travelers": traveler_list,
                "cabin": cabin,
                "price": price,
                "currency": currency,
                "followup_due": _default_followup(depart_date, return_date),
            })
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Logged trip '{result['id']}' ({result.get('route')})."

    @mcp.tool()
    def list_trips() -> str:
        """List all trips in history (most recent first)."""
        try:
            trips = profile_api.list_trips()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if not trips:
            return "No trips logged yet. Use log_trip after booking."
        lines = [_fmt_trip(t) for t in trips]
        lines.append(f"\n{len(trips)} trip(s).")
        return "\n".join(lines)

    @mcp.tool()
    def get_trip(id: str) -> str:
        """Get one trip by ID.

        Args:
            id: Trip ID (see list_trips)
        """
        try:
            t = profile_api.get_trip(id)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if "error" in t:
            return f"No trip '{id}'."
        return _fmt_trip(t)

    @mcp.tool()
    def trips_pending_followup() -> str:
        """List trips that have completed (or returned) and not yet been followed up.

        Use to proactively ask the user how a trip went, then record_trip_feedback.
        """
        try:
            trips = profile_api.trips_followup()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if not trips:
            return "No trips pending follow-up."
        lines = ["Trips ready for follow-up:"]
        lines.extend(_fmt_trip(t) for t in trips)
        lines.append("\nAsk how each went, then call record_trip_feedback.")
        return "\n".join(lines)

    @mcp.tool()
    def record_trip_feedback(
        id: str,
        feedback: str,
        learnings: str | None = None,
    ) -> str:
        """Record post-trip feedback and fold lasting lessons into preferences.

        Marks the trip followed-up. Any 'learnings' are appended to the user's
        travel preferences so future recommendations improve.

        Args:
            id: Trip ID (see trips_pending_followup / list_trips)
            feedback: What the user said about the trip
            learnings: Comma-separated durable preferences learned, e.g.
                'prefers aisle on long-haul, dislikes early departures'
        """
        learn_list = None
        if learnings:
            learn_list = [s.strip() for s in learnings.split(",") if s.strip()]
        try:
            result = profile_api.trip_feedback(id, feedback, learn_list)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if "error" in result:
            return f"Error: {result['error']}"
        out = [f"Recorded feedback for trip '{id}'."]
        if learn_list:
            out.append(f"Added {len(learn_list)} learning(s) to your preferences.")
        return "\n".join(out)
