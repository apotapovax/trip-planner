"""Credit card & points tools for FlightClaw — backend (D1) backed.

Records which cards and loyalty/points balances the user holds, for award and
transfer-partner awareness. NOTE: actual payment still routes through Link
virtual cards (duffel_book_with_link) — these records are for optimization, not
charging. Pair with the Card Links MCP (transfer partners) and the Award Travel
Finder MCP (award availability) at the agent layer.
"""

import profile_api


def register_cards_tools(mcp):
    """Register card + points tools on the MCP server."""

    @mcp.tool()
    def save_card(
        id: str,
        issuer: str,
        product: str,
        network: str | None = None,
        region: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Save or update a credit card the user holds (for points/transfer awareness).

        Args:
            id: Short slug, e.g. 'amex-plat'
            issuer: Card issuer, e.g. 'American Express'
            product: Product name, e.g. 'Platinum'
            network: amex | visa | mastercard (optional)
            region: Card region, e.g. US, UK, AU (optional)
            notes: Free-form notes, e.g. transfer partners or perks (optional)
        """
        try:
            result = profile_api.upsert_card({
                "id": id, "issuer": issuer, "product": product,
                "network": network, "region": region, "notes": notes,
            })
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Saved card '{id.lower().strip()}'. {len(result.get('cards', []))} card(s) on file."

    @mcp.tool()
    def list_cards() -> str:
        """List the user's saved cards and points balances."""
        try:
            data = profile_api.get_cards()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        cards = data.get("cards", [])
        points = data.get("points", [])
        if not cards and not points:
            return "No cards or points saved. Use save_card / set_points_balance."
        lines = []
        if cards:
            lines.append("Cards:")
            for c in cards:
                line = f"  {c['id']}: {c['issuer']} {c['product']}"
                if c.get("network"):
                    line += f" ({c['network']})"
                if c.get("region"):
                    line += f" [{c['region']}]"
                if c.get("notes"):
                    line += f" — {c['notes']}"
                lines.append(line)
        if points:
            lines.append("Points balances:")
            for p in points:
                lines.append(f"  {p['program']}: {p['balance']:,}")
        return "\n".join(lines)

    @mcp.tool()
    def delete_card(id: str) -> str:
        """Delete a saved card.

        Args:
            id: Card slug to delete (e.g. 'amex-plat')
        """
        try:
            result = profile_api.delete_card(id)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if not result.get("ok"):
            return f"No card '{id}' found."
        return f"Deleted card '{id}'."

    @mcp.tool()
    def set_points_balance(program: str, balance: int) -> str:
        """Set or update a loyalty/points balance.

        Args:
            program: Program name (e.g. 'Amex Membership Rewards', 'Avios', 'United MileagePlus')
            balance: Current points/miles balance
        """
        try:
            profile_api.set_points(program, balance)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        return f"Set {program} balance to {balance:,}."

    @mcp.tool()
    def list_points() -> str:
        """List the user's loyalty/points balances."""
        try:
            data = profile_api.get_cards()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        points = data.get("points", [])
        if not points:
            return "No points balances saved. Use set_points_balance."
        lines = [f"  {p['program']}: {p['balance']:,}" for p in points]
        return "Points balances:\n" + "\n".join(lines)
