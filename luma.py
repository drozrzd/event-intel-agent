import re
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from security import sanitize, validate_linkedin_url
from llm import call_groq, parse_json_response
from memory import is_event_seen

LUMA_API = "https://api.lu.ma/discover/get-paginated-events"

MAX_NAME_CHARS = 100
MAX_TITLE_CHARS = 80
MAX_COMPANY_CHARS = 80
MAX_BIO_CHARS = 150
MAX_ORGANIZER_DESC = 300
MAX_CLASSIFY_BLOCK = 4000

LIFESTYLE_KEYWORDS = [
    "yoga", "sauna", "hike", "hiking", "hockey", "cooking",
    "meditation", "drawing", "craft", "wellness", "fitness",
    "gallery", "museum", "triathlon", "pilates", "soccer", "football",
    "martial arts", "surfskate", "sober party", "offline club", "paint night",
]

STARTUP_KEYWORDS = [
    "startup", "founder", "investor", "vc", "venture", "capital", "pitch",
    "demo", "hackathon", "accelerator", "operator", "ai", "tech", "builder",
    "engineer", "product", "seed", "raise", "funding", "innovation",
    "incubator", "entrepreneur", "saas", "software",
]

ALLOWED_URL_DOMAINS = ["lu.ma", "partiful.com", "tnt.so", "eventbrite.com"]

IMS_CONTEXT = """
Imaginary Space (IMS) is a technical execution partner for early-stage startups.
They build MVPs for founders who have funding but need to move fast.
Score events HIGHER: pre-seed to Seed founders, AI/SaaS/developer products,
VC partners, accelerator cohorts (YC, Techstars, MassChallenge, TNT).
Score events LOWER: Series B+ employees, non-technical CPG/retail, academics.
""".strip()

_CLASSIFY_SYSTEM = f"""You are a classifier for a Boston startup ecosystem event digest.
{IMS_CONTEXT}
You receive a numbered list: [N] EventName | OrganizerName
Return a JSON array of integer IDs (0-indexed) to include.
Include: clearly involves founders, investors, operators, or technical builders in Boston.
Reject: generic tech meetups, academic seminars, wellness/lifestyle events.
Return ONLY a JSON array like [0, 3, 5] or []. No other text."""


def fetch_luma_events(config: dict) -> list:
    events_out = []
    cursor = None
    now_utc = datetime.now(timezone.utc)
    window_days = config.get("window_days_attendees", 3)
    window_end = now_utc + timedelta(days=window_days)

    while True:
        params = {
            "geo_latitude": config["geo_latitude"],
            "geo_longitude": config["geo_longitude"],
            "geo_radius_km": config["geo_radius_km"],
            "pagination_limit": 50,
        }
        if cursor:
            params["pagination_cursor"] = cursor
        try:
            resp = requests.get(LUMA_API, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[LUMA] Fetch error: {e}")
            break

        entries = data.get("entries", [])
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")
        stop = False

        for entry in entries:
            start_at = entry.get("event", {}).get("start_at", "")
            try:
                start_dt = datetime.fromisoformat(
                    start_at.replace("Z", "+00:00")
                )
                if start_dt > window_end:
                    stop = True
                    break
                events_out.append(entry)
            except (ValueError, AttributeError):
                pass  # bad date = skip, not append

        if not has_more or not cursor or stop:
            break

    print(f"[LUMA] Fetched {len(events_out)} raw entries")
    return events_out


def normalize_luma_event(entry: dict) -> dict | None:
    event = entry.get("event", {})
    name = sanitize(event.get("name", ""))
    if not name or name == "[REDACTED]":
        return None

    url_slug = event.get("url", "")
    if not url_slug:
        return None
    full_url = url_slug if url_slug.startswith("http") else f"https://lu.ma/{url_slug}"

    parsed = urlparse(full_url)
    if not any(parsed.netloc.endswith(domain) for domain in ALLOWED_URL_DOMAINS):
        return None

    calendar = entry.get("calendar") or {}
    ticket_info = entry.get("ticket_info") or {}

    api_id = event.get("api_id", "")

    return {
        "name": name,
        "api_id": api_id,
        "start_at": event.get("start_at", ""),
        "url": full_url,
        "source": "luma",
        "location_type": event.get("location_type", "offline"),
        "organizer_name": sanitize(calendar.get("name", "")),
        "organizer_desc": sanitize(
            calendar.get("description_short", ""), max_chars=MAX_ORGANIZER_DESC
        ),
        "guest_count": int(entry.get("guest_count") or 0),
        "require_approval": bool(ticket_info.get("require_approval")),
        "verified": bool(calendar.get("verified_at")),
    }


def pre_filter(event_list: list, memory: dict, config: dict) -> list:
    now_utc = datetime.now(timezone.utc)
    window_end = now_utc + timedelta(days=config.get("window_days_attendees", 3))
    extra_deny = [k.lower() for k in config.get("extra_keyword_deny", [])]
    extra_allow = [k.lower() for k in config.get("extra_keyword_allow", [])]

    results = []
    for event in event_list:
        name_lower = event["name"].lower()
        org_lower = (
            event.get("organizer_name", "") + " " + event.get("organizer_desc", "")
        ).lower()

        # Date gate
        start_at = event.get("start_at", "")
        try:
            start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
            if start_dt <= now_utc or start_dt > window_end:
                continue
        except (ValueError, AttributeError):
            continue

        # Online drop
        if event.get("location_type") == "online":
            continue

        # Too small
        if event.get("guest_count", 0) < 10:
            continue

        # Duplicate
        if is_event_seen(memory, event.get("url", "")):
            continue

        # Lifestyle drop
        lifestyle_hit = any(
            re.search(r"\b" + re.escape(kw) + r"\b", name_lower, re.IGNORECASE)
            for kw in LIFESTYLE_KEYWORDS + extra_deny
        )
        if lifestyle_hit:
            continue

        # Startup keyword gate
        searchable = name_lower + " " + org_lower
        all_kw = STARTUP_KEYWORDS + extra_allow
        if not any(kw in searchable for kw in all_kw):
            continue

        results.append(event)

    return results


def classify_events(event_list: list, call_groq=call_groq) -> list:
    if not event_list:
        return []
    lines = [
        f"[{i}] {e.get('name', '')[:100]} | {e.get('organizer_name', '')[:60]}"
        for i, e in enumerate(event_list)
    ]
    block = "\n".join(lines)[:MAX_CLASSIFY_BLOCK]
    raw = call_groq(_CLASSIFY_SYSTEM, block)
    approved_ids = parse_json_response(raw, fallback=list(range(len(event_list))))
    if not isinstance(approved_ids, list):
        approved_ids = list(range(len(event_list)))
    valid = [i for i in approved_ids if isinstance(i, int) and 0 <= i < len(event_list)]
    result = [event_list[i] for i in valid]
    print(f"[CLASSIFY] {len(result)}/{len(event_list)} events approved")
    return result


def fetch_event_hosts(raw_entries: list, max_per_event: int = 5) -> list:
    """Extract host/organizer profiles from already-fetched discover API entries.

    Luma embeds host profiles in every event entry — name, bio, linkedin_handle.
    This replaces guest-list fetching (which is organizer-only and returns 403).
    Hosts are event organizers: founders, community builders, high-signal contacts.
    """
    hosts = []
    seen_names: set = set()

    for entry in raw_entries:
        event = entry.get("event", {})
        event_name = sanitize(event.get("name", ""), max_chars=100)
        event_url = event.get("url", "")
        if event_url and not event_url.startswith("http"):
            event_url = f"https://lu.ma/{event_url}"

        raw_hosts = entry.get("hosts", []) or []
        count = 0
        for h in raw_hosts:
            if count >= max_per_event:
                break

            name = sanitize(h.get("name", ""), max_chars=MAX_NAME_CHARS)
            if not name or name == "[REDACTED]" or name in seen_names:
                continue
            # Skip org accounts — person names always have a space
            if " " not in name.strip():
                continue

            # Build LinkedIn URL from handle — skip company pages
            linkedin_handle = (h.get("linkedin_handle") or "").strip()
            linkedin_url = None
            if linkedin_handle and linkedin_handle.startswith("/in/"):
                candidate = f"https://www.linkedin.com{linkedin_handle}"
                if validate_linkedin_url(candidate):
                    linkedin_url = candidate

            seen_names.add(name)
            hosts.append({
                "name": name,
                "username": h.get("username", "") or "",
                "title": sanitize(h.get("job_title", "") or h.get("title", ""), max_chars=MAX_TITLE_CHARS),
                "company": sanitize(h.get("company", "") or "", max_chars=MAX_COMPANY_CHARS),
                "bio": sanitize(h.get("bio_short", "") or "", max_chars=MAX_BIO_CHARS),
                "linkedin": linkedin_url,
                "linkedin_source": "luma_profile" if linkedin_url else None,
                "score": 5,  # neutral default; scorer upgrades if profile has data
                "signals": [f"Organizer of '{event_name}'"],
                "event": event_name,
                "is_speaker": False,
            })
            count += 1

    print(f"[LUMA_HOSTS] {len(hosts)} hosts extracted from {len(raw_entries)} events")
    return hosts
