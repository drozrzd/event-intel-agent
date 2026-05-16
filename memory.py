import json
import os
import re
from datetime import date, timedelta
from typing import Optional

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "memory.json")


def load_memory() -> dict:
    if not os.path.exists(MEMORY_PATH):
        return {
            "processed_contacts": [],
            "processed_events": [],
            "last_run": None,
            "luma_session": None,
        }
    with open(MEMORY_PATH) as f:
        return json.load(f)


def save_memory(memory: dict) -> None:
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)


def trim_memory(
    memory: dict,
    contact_expiry_days: int = 30,
    event_expiry_days: int = 7,
) -> dict:
    contact_cutoff = (date.today() - timedelta(days=contact_expiry_days)).isoformat()
    event_cutoff = (date.today() - timedelta(days=event_expiry_days)).isoformat()

    memory["processed_contacts"] = [
        c for c in memory.get("processed_contacts", [])
        if c.get("date_seen", "9999-99-99") >= contact_cutoff
    ]
    memory["processed_events"] = [
        e for e in memory.get("processed_events", [])
        if e.get("date_seen", "9999-99-99") >= event_cutoff
    ]
    return memory


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")


def make_contact_key(linkedin_url: Optional[str], name: str, company: str) -> str:
    if linkedin_url:
        return linkedin_url.lower().rstrip("/")
    return f"name:{_slug(name or 'unknown')}:{_slug(company or 'unknown')}"


def is_contact_seen(memory: dict, key: str) -> bool:
    seen = {c["key"] for c in memory.get("processed_contacts", [])}
    return key in seen


def mark_contact_seen(memory: dict, key: str, name: str) -> None:
    memory.setdefault("processed_contacts", []).append({
        "key": key,
        "name": name,
        "date_seen": date.today().isoformat(),
    })


def is_event_seen(memory: dict, url: str) -> bool:
    seen = {e["url"] for e in memory.get("processed_events", [])}
    return url in seen


def mark_event_seen(memory: dict, url: str) -> None:
    memory.setdefault("processed_events", []).append({
        "url": url,
        "date_seen": date.today().isoformat(),
    })
