import os
import requests
from datetime import datetime, timedelta, timezone
from security import sanitize

EVENTBRITE_API = "https://www.eventbriteapi.com/v3/events/search/"
ALLOWED_URL_DOMAINS = ["eventbrite.com"]


def fetch_eventbrite_events(config: dict) -> list:
    token = os.environ.get("EVENTBRITE_TOKEN", "")
    if not token:
        print("[EVENTBRITE] No token, skipping")
        return []

    window_days = config.get("window_days_attendees", 3)
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=window_days)
    radius_mi = int(config.get("geo_radius_km", 30) * 0.621)

    params = {
        "location.address": "Boston, MA",
        "location.within": f"{radius_mi}mi",
        "q": "startup founder tech AI",
        "start_date.range_start": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_date.range_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expand": "organizer",
    }
    try:
        resp = requests.get(
            EVENTBRITE_API,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json().get("events", [])
        normalized = [normalize_eventbrite_event(e) for e in raw]
        result = [e for e in normalized if e]
        print(f"[EVENTBRITE] {len(result)} events")
        return result
    except Exception as e:
        print(f"[EVENTBRITE] Error: {e}")
        return []


def normalize_eventbrite_event(entry: dict) -> dict | None:
    url = entry.get("url", "")
    if not url or not any(d in url for d in ALLOWED_URL_DOMAINS):
        return None

    name_field = entry.get("name", {})
    name = sanitize(
        name_field.get("text", "") if isinstance(name_field, dict) else "", max_chars=200
    )
    if not name:
        return None

    organizer = entry.get("organizer") or {}
    desc_field = organizer.get("description") or {}
    org_desc = sanitize(
        desc_field.get("text", "") if isinstance(desc_field, dict) else "", max_chars=300
    )

    return {
        "name": name,
        "api_id": "",  # Eventbrite events skip guest extraction
        "start_at": (entry.get("start") or {}).get("utc", ""),
        "url": url,
        "source": "eventbrite",
        "location_type": "offline",
        "organizer_name": sanitize(organizer.get("name", "")),
        "organizer_desc": org_desc,
        "guest_count": entry.get("capacity") or 0,
        "require_approval": not entry.get("listed", True),
        "verified": False,
    }
