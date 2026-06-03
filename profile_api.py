"""HTTP client for the flightclaw-api profile store (D1-backed).

Reuses the same FLIGHTCLAW_API_URL / FLIGHTCLAW_API_KEY config as duffel_api.py.
All profile data (travelers, preferences, cards/points, groups, trips) lives
server-side so it is shared across sessions and devices.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class ProfileError(RuntimeError):
    pass


# Tenant the client operates on. None → server default ('default', the single-user
# path). Set via set_tenant() or the FLIGHTCLAW_TENANT env var to scope to another
# tenant (e.g. when driving a specific user from a script).
_TENANT = os.environ.get("FLIGHTCLAW_TENANT") or None


def set_tenant(tenant):
    """Scope all subsequent calls to a tenant (None = server default)."""
    global _TENANT
    _TENANT = tenant or None


def is_configured():
    return bool(
        os.environ.get("FLIGHTCLAW_API_URL")
        and os.environ.get("FLIGHTCLAW_API_KEY")
    )


def _request(method, path, body=None, params=None):
    base_url = os.environ.get("FLIGHTCLAW_API_URL", "").rstrip("/")
    api_key = os.environ.get("FLIGHTCLAW_API_KEY", "")
    if not base_url or not api_key:
        raise ProfileError(
            "FLIGHTCLAW_API_URL and FLIGHTCLAW_API_KEY must be set "
            "(they point to your private flightclaw-api Worker)."
        )

    url = f"{base_url}{path}"
    if params:
        qs = "&".join(
            f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v is not None
        )
        if qs:
            url = f"{url}?{qs}"

    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "flightclaw/1.0",
    }
    if _TENANT:
        headers["X-Tenant"] = _TENANT
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            msg = json.loads(error_body).get("error", error_body)
        except json.JSONDecodeError:
            msg = error_body
        raise ProfileError(f"API error ({e.code}): {msg}")
    except urllib.error.URLError as e:
        raise ProfileError(f"Could not reach flightclaw-api: {e}")


# --- Whole profile ---

def get_profile():
    return _request("GET", "/profile")


# --- Travelers ---

def list_travelers():
    return _request("GET", "/profile/travelers").get("travelers", [])


def get_traveler(name):
    return _request("GET", "/profile/traveler", params={"name": name})


def upsert_traveler(traveler):
    return _request("POST", "/profile/traveler", traveler)


def delete_traveler(name):
    return _request("POST", "/profile/traveler/delete", {"name": name})


# --- Me ---

def get_me():
    return _request("GET", "/profile/me")


def set_me(me_traveler=None, account_email=None, home_airports=None):
    body = {}
    if me_traveler is not None:
        body["me_traveler"] = me_traveler
    if account_email is not None:
        body["account_email"] = account_email
    if home_airports is not None:
        body["home_airports"] = home_airports
    return _request("POST", "/profile/me", body)


# --- Preferences ---

def get_preferences():
    return _request("GET", "/profile/preferences")


def patch_preferences(patch):
    return _request("POST", "/profile/preferences", patch)


# --- Cards & points ---

def get_cards():
    return _request("GET", "/profile/cards")


def upsert_card(card):
    return _request("POST", "/profile/card", card)


def delete_card(card_id):
    return _request("POST", "/profile/card/delete", {"id": card_id})


def set_points(program, balance):
    return _request("POST", "/profile/points", {"program": program, "balance": balance})


# --- Groups ---

def get_groups():
    return _request("GET", "/profile/groups").get("groups", [])


def upsert_group(name, members):
    return _request("POST", "/profile/group", {"name": name, "members": members})


def delete_group(name):
    return _request("POST", "/profile/group/delete", {"name": name})


# --- Trips ---

def list_trips():
    return _request("GET", "/profile/trips").get("trips", [])


def get_trip(trip_id):
    return _request("GET", "/profile/trip", params={"id": trip_id})


def upsert_trip(trip):
    return _request("POST", "/profile/trip", trip)


def trips_followup():
    return _request("GET", "/profile/trips/followup").get("trips", [])


def trip_feedback(trip_id, feedback, learnings=None):
    body = {"id": trip_id, "feedback": feedback}
    if learnings:
        body["learnings"] = learnings
    return _request("POST", "/profile/trip/feedback", body)
