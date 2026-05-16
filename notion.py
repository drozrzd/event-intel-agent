import os
import requests
from datetime import date

_API_VER = "2022-06-28"
_BASE = "https://api.notion.com/v1"


def _headers() -> dict:
    token = os.environ.get("NOTION_API_KEY", "")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _API_VER,
        "Content-Type": "application/json",
    }


def _rich(text: str) -> list:
    return [{"type": "text", "text": {"content": (text or "")[:2000]}}]


def save_contacts_to_notion(contacts: list) -> None:
    db_id = os.environ.get("NOTION_DATABASE_ID", "")
    if not db_id:
        print("[NOTION] No NOTION_DATABASE_ID set, skipping")
        return

    token = os.environ.get("NOTION_API_KEY", "")
    if not token:
        print("[NOTION] No NOTION_API_KEY set, skipping")
        return

    today = date.today().isoformat()
    linkedin_contacts = [c for c in contacts if c.get("linkedin")]
    ok = 0

    for c in linkedin_contacts:
        name = (c.get("name") or "").strip()
        if not name:
            continue

        raw_signals = [
            s for s in c.get("signals", [])
            if s.lower().strip() not in ("unknown", "organizer", "n/a", "none", "")
        ]
        position_text = " · ".join(raw_signals)

        props = {
            "Name": {"title": _rich(name)},
            "LinkedIn URL": {"url": c.get("linkedin")},
            "Stage": {"select": {"name": "Events"}},
            "Last Signal": {"date": {"start": today}},
        }

        if c.get("event"):
            props["Context"] = {"rich_text": _rich(c["event"])}
        if position_text:
            props["Position"] = {"rich_text": _rich(position_text)}
        if c.get("company"):
            props["Company"] = {"rich_text": _rich(c["company"])}
        if isinstance(c.get("score"), (int, float)):
            props["EV"] = {"number": c["score"]}

        body = {
            "parent": {"database_id": db_id},
            "properties": props,
        }

        try:
            resp = requests.post(
                f"{_BASE}/pages",
                headers=_headers(),
                json=body,
                timeout=15,
            )
            if resp.status_code in (200, 201):
                ok += 1
            else:
                print(f"[NOTION] {name}: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"[NOTION] {name}: {e}")

    print(f"[NOTION] {ok}/{len(linkedin_contacts)} contacts saved")
