import os
from datetime import datetime, timezone

from config import load_config
from memory import (
    load_memory, save_memory, trim_memory,
    make_contact_key, is_contact_seen, mark_contact_seen,
    is_event_seen, mark_event_seen,
)
from llm import call_groq
from luma import (
    fetch_luma_events, normalize_luma_event,
    pre_filter, classify_events,
    fetch_event_hosts,
)
from venues import scrape_venue_speakers
from enrichment import enrich_linkedin, enrich_funding
from scoring import score_attendees
from eventbrite import fetch_eventbrite_events
from discord_notifier import format_message, review_message, post_to_discord

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def main():
    print(f"[START] Event Intel Agent — {datetime.now(timezone.utc).isoformat()}")
    if DRY_RUN:
        print("[DRY_RUN] Active — no Discord post, no memory write")

    config = load_config()
    memory = load_memory()
    memory = trim_memory(
        memory,
        config.get("memory_contact_expiry_days", 30),
        config.get("memory_event_expiry_days", 7),
    )

    all_contacts = []
    captcha_count = [0]
    max_captcha = config.get("google_captcha_abort_threshold", 3)

    # ── Pipeline A: Luma Event Hosts ─────────────────────────────────────────
    # Luma guest lists are organizer-only (403 for non-organizers).
    # We extract event hosts instead — organizers are high-signal founders/builders
    # and their LinkedIn handles come pre-populated from the Luma profile API.
    luma_raw = fetch_luma_events(config)
    eb_raw = fetch_eventbrite_events(config)

    events = []
    for entry in luma_raw:
        norm = normalize_luma_event(entry)
        if norm:
            events.append(norm)
    for entry in eb_raw:
        if entry:
            events.append(entry)

    filtered_events = pre_filter(events, memory, config)
    filtered_events = classify_events(filtered_events)
    print(f"[PIPELINE_A] {len(filtered_events)} qualifying events")

    # Build lookup: api_id → raw entry (hosts are in raw entries, not normalized)
    raw_by_id = {e.get("event", {}).get("api_id", ""): e for e in luma_raw}
    qualifying_raw = [
        raw_by_id[ev["api_id"]]
        for ev in filtered_events
        if ev.get("api_id") and ev["api_id"] in raw_by_id
    ]

    hosts = fetch_event_hosts(qualifying_raw, max_per_event=config.get("max_hosts_per_event", 5))

    attendees = []
    for h in hosts:
        # Only Google-search if no LinkedIn and has a real username to search for
        if not h.get("linkedin") and h.get("username") and captcha_count[0] < max_captcha:
            h = enrich_linkedin(h, config, captcha_count)
        h = enrich_funding(h)
        key = make_contact_key(h.get("linkedin"), h.get("name", ""), h.get("company", ""))
        if not is_contact_seen(memory, key):
            attendees.append(h)

    for ev in filtered_events:
        mark_event_seen(memory, ev.get("url", ""))

    # Score hosts in batch
    attendees = score_attendees(attendees, call_groq)
    min_score = config.get("min_score_attendees", 6)
    attendees = [a for a in attendees if a.get("score", 0) >= min_score]
    print(f"[PIPELINE_A] {len(attendees)} event hosts after score filter")
    all_contacts.extend(attendees)

    # ── Pipeline B: Venue Speakers ────────────────────────────────────────────
    speakers = scrape_venue_speakers(config)
    for s in speakers:
        if captcha_count[0] < max_captcha:
            s = enrich_linkedin(s, config, captcha_count)
        s = enrich_funding(s)
        key = make_contact_key(s.get("linkedin"), s.get("name", ""), s.get("company", ""))
        if not is_contact_seen(memory, key):
            all_contacts.append(s)

    print(f"[PIPELINE_B] {len(speakers)} speakers found")

    # ── Combined output ───────────────────────────────────────────────────────
    if not all_contacts:
        print("[RESULT] No qualifying contacts. Exiting silently.")
        memory["last_run"] = datetime.now(timezone.utc).isoformat()
        if not DRY_RUN:
            save_memory(memory)
        return

    all_contacts = all_contacts[:config.get("max_contacts_per_run", 60)]
    message = format_message(all_contacts)
    print(f"[FORMAT] {len(message)} chars")

    verdict = review_message(message)
    if verdict.get("verdict") != "APPROVED":
        print(f"[REVIEW] REJECTED: {verdict.get('reason', 'unknown')}")
        if DRY_RUN:
            print(f"[DRY_RUN] Rejected message was:\n{message}")
    else:
        print("[REVIEW] APPROVED")
        if not DRY_RUN:
            post_to_discord(message)
        else:
            print(f"[DRY_RUN] Would post:\n{message}")

    # Mark contacts as seen
    for c in all_contacts:
        key = make_contact_key(c.get("linkedin"), c.get("name", ""), c.get("company", ""))
        mark_contact_seen(memory, key, c.get("name", ""))

    memory["last_run"] = datetime.now(timezone.utc).isoformat()
    if not DRY_RUN:
        save_memory(memory)

    confirmed = sum(1 for c in all_contacts if c.get("linkedin"))
    print(f"[DONE] {len(all_contacts)} contacts · {confirmed} LinkedIn confirmed")


if __name__ == "__main__":
    main()
