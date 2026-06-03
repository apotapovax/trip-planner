"""Companion group tools for FlightClaw — backend (D1) backed.

Save named groups of travelers (e.g. 'family' = jack,jane,kid) so "who are you
travelling with?" is answered once and reused. Group members are traveler name
keys; pass the returned comma-separated names straight to booking tools.
"""

import profile_api


def register_group_tools(mcp):
    """Register companion-group tools on the MCP server."""

    @mcp.tool()
    def save_group(name: str, members: str) -> str:
        """Save or update a named travel group for reuse.

        Args:
            name: Group name, e.g. 'family' or 'work-trip'
            members: Comma-separated traveler name keys (e.g. 'jack,jane'). Save each
                traveler with save_traveler first.
        """
        member_list = [m.strip().lower() for m in members.split(",") if m.strip()]
        if not member_list:
            return "Provide at least one member name."
        # Warn about unknown members but still save (they can be added later).
        try:
            known = {t["name"] for t in profile_api.list_travelers()}
            unknown = [m for m in member_list if m not in known]
            result = profile_api.upsert_group(name, member_list)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        out = f"Saved group '{result['name']}': {', '.join(result['members'])}."
        if unknown:
            out += f"\nWarning: not yet saved as travelers: {', '.join(unknown)} (use save_traveler)."
        return out

    @mcp.tool()
    def list_groups() -> str:
        """List all saved travel groups."""
        try:
            groups = profile_api.get_groups()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if not groups:
            return "No groups saved. Use save_group."
        lines = [f"  {g['name']}: {', '.join(g['members']) or '(empty)'}" for g in groups]
        lines.append(f"\n{len(groups)} group(s).")
        return "\n".join(lines)

    @mcp.tool()
    def get_group(name: str) -> str:
        """Get a group's members, ready to pass to booking/recommendation tools.

        Returns the comma-separated traveler names (use directly as the `passengers`
        argument of duffel_book_with_link / duffel_book_flight) plus a roster.

        Args:
            name: Group name (e.g. 'family')
        """
        key = name.lower().strip()
        try:
            groups = profile_api.get_groups()
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        group = next((g for g in groups if g["name"] == key), None)
        if not group:
            return f"No group '{key}'. Use list_groups to see saved groups."
        members = group["members"]
        if not members:
            return f"Group '{key}' has no members."
        lines = [f"Group '{key}' — passengers: {','.join(members)}", "Roster:"]
        for m in members:
            try:
                t = profile_api.get_traveler(m)
            except profile_api.ProfileError:
                t = {"error": "lookup failed"}
            if "error" in t:
                lines.append(f"  {m}: (not found — save with save_traveler)")
            else:
                lines.append(f"  {m}: {t['given_name']} {t['family_name']}")
        return "\n".join(lines)

    @mcp.tool()
    def delete_group(name: str) -> str:
        """Delete a saved travel group.

        Args:
            name: Group name to delete
        """
        try:
            result = profile_api.delete_group(name)
        except profile_api.ProfileError as e:
            return f"Error: {e}"
        if not result.get("ok"):
            return f"No group '{name}' found."
        return f"Deleted group '{name}'."
