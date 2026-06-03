"""Traveler profile tools for FlightClaw — backend (D1) backed.

Replaces the local-JSON passenger_profiles.py. Stores travelers (you + your
companions) server-side so they're reusable across sessions and ready for
booking by name. Also tracks who "you" are (the primary user).
"""

import json

import profile_api


def _fmt_traveler(p):
    line = f"{p['name']}: {p['given_name']} {p['family_name']}"
    if p.get("relationship"):
        line += f" ({p['relationship']})"
    if p.get("is_me"):
        line += " [you]"
    if p.get("email"):
        line += f" — {p['email']}"
    if p.get("loyalty_programmes"):
        progs = ", ".join(
            f"{lp.get('airline_iata_code')}:{lp.get('account_number')}"
            for lp in p["loyalty_programmes"]
        )
        line += f" | Loyalty: {progs}"
    return line


def register_traveler_tools(mcp):
    """Register traveler + primary-user tools on the MCP server."""

    @mcp.tool()
    def save_traveler(
        name: str,
        given_name: str,
        family_name: str,
        born_on: str | None = None,
        gender: str | None = None,
        title: str | None = None,
        email: str | None = None,
        phone_number: str | None = None,
        passport_number: str | None = None,
        passport_expiry: str | None = None,
        passport_nationality: str | None = None,
        relationship: str | None = None,
        loyalty_programmes: list | None = None,
    ) -> str:
        """Save or update a traveler profile (you or a companion) for reuse and booking.

        Args:
            name: Short lookup key, e.g. 'jack' or 'jane'
            given_name: First/given name as on passport
            family_name: Last/family name as on passport
            born_on: Date of birth (YYYY-MM-DD)
            gender: 'm' or 'f'
            title: 'mr', 'mrs', 'ms', 'miss', 'dr'
            email: Contact email
            phone_number: Phone with country code (e.g. +447700000000)
            passport_number: Passport number (optional)
            passport_expiry: Passport expiry YYYY-MM-DD (optional)
            passport_nationality: Passport nationality ISO code (optional)
            relationship: Relationship to you (self, spouse, partner, child, parent, friend, colleague)
            loyalty_programmes: List of {airline_iata_code, account_number} (optional)
        """
        try:
            result = profile_api.upsert_traveler({
                "name": name,
                "given_name": given_name,
                "family_name": family_name,
                "born_on": born_on,
                "gender": gender.lower().strip() if gender else None,
                "title": title.lower().strip() if title else None,
                "email": email,
                "phone_number": phone_number,
                "passport_number": passport_number,
                "passport_expiry": passport_expiry,
                "passport_nationality": passport_nationality,
                "relationship": relationship,
                "loyalty_programmes": loyalty_programmes or [],
            })
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Saved traveler '{result['name']}'."

    @mcp.tool()
    def list_travelers() -> str:
        """List all saved traveler profiles (you and your companions)."""
        try:
            travelers = profile_api.list_travelers()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if not travelers:
            return "No travelers saved. Use save_traveler to add one."
        lines = [_fmt_traveler(p) for p in travelers]
        lines.append(f"\n{len(travelers)} traveler(s) saved.")
        return "\n".join(lines)

    @mcp.tool()
    def get_traveler(name: str) -> str:
        """Get a traveler profile by name. Returns JSON usable for booking.

        Args:
            name: Short name key (e.g. 'jack')
        """
        try:
            p = profile_api.get_traveler(name)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if "error" in p:
            return f"No traveler '{name}'. Use list_travelers to see saved profiles."
        return json.dumps(p, indent=2)

    @mcp.tool()
    def delete_traveler(name: str) -> str:
        """Delete a saved traveler profile.

        Args:
            name: Short name key to delete (e.g. 'jane')
        """
        try:
            result = profile_api.delete_traveler(name)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if not result.get("ok"):
            return f"No traveler '{name}' found."
        return f"Deleted traveler '{name}'."

    @mcp.tool()
    def set_me(
        traveler: str,
        account_email: str | None = None,
        home_airports: str | None = None,
    ) -> str:
        """Mark which saved traveler is YOU (the primary user) and set account defaults.

        Save your own traveler with save_traveler first, then call this with that name.

        Args:
            traveler: Name key of your own traveler profile (e.g. 'jack')
            account_email: Your primary contact email (optional)
            home_airports: Comma-separated home/preferred origin airports (e.g. 'LHR,LGW')
        """
        airports = None
        if home_airports:
            airports = [a.strip().upper() for a in home_airports.split(",") if a.strip()]
        try:
            result = profile_api.set_me(
                me_traveler=traveler,
                account_email=account_email,
                home_airports=airports,
            )
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        lines = [f"You are set as '{result.get('me_traveler')}'."]
        if result.get("home_airports"):
            lines.append(f"Home airports: {', '.join(result['home_airports'])}")
        if result.get("account_email"):
            lines.append(f"Account email: {result['account_email']}")
        return "\n".join(lines)

    @mcp.tool()
    def get_me() -> str:
        """Show the primary user (you): linked traveler, account email, home airports."""
        try:
            me = profile_api.get_me()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if not me.get("me_traveler"):
            return "No primary user set. Use set_me after saving your traveler profile."
        lines = [f"You: {me['me_traveler']}"]
        if me.get("home_airports"):
            lines.append(f"Home airports: {', '.join(me['home_airports'])}")
        if me.get("account_email"):
            lines.append(f"Account email: {me['account_email']}")
        if me.get("traveler"):
            lines.append("\n" + _fmt_traveler(me["traveler"]))
        return "\n".join(lines)

    @mcp.tool()
    def import_local_passengers() -> str:
        """One-time migration: push any local data/passengers.json profiles to the backend."""
        try:
            from passenger_profiles import load_passengers
        except ImportError:
            return "No local passenger module found."
        local = load_passengers()
        if not local:
            return "No local passengers to import."
        imported, errors = 0, []
        for p in local:
            try:
                res = profile_api.upsert_traveler({
                    "name": p.get("name"),
                    "given_name": p.get("given_name"),
                    "family_name": p.get("family_name"),
                    "born_on": p.get("born_on"),
                    "gender": p.get("gender"),
                    "title": p.get("title"),
                    "email": p.get("email"),
                    "phone_number": p.get("phone_number"),
                    "passport_number": p.get("passport_number"),
                    "passport_expiry": p.get("passport_expiry"),
                    "passport_nationality": p.get("passport_nationality"),
                    "loyalty_programmes": p.get("loyalty_programmes") or [],
                })
                if "error" in res:
                    errors.append(f"{p.get('name')}: {res['error']}")
                else:
                    imported += 1
            except profile_api.ProfileError as e:
                errors.append(f"{p.get('name')}: {e}")
        out = [f"Imported {imported} traveler(s) to the backend."]
        if errors:
            out.append("Errors:\n  " + "\n  ".join(errors))
        return "\n".join(out)
