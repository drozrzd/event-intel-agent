import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from luma import normalize_luma_event, pre_filter, fetch_event_hosts, ALLOWED_URL_DOMAINS

# ── normalize_luma_event ──────────────────────────────────────────────────────

def _make_entry(name="AI Founders Breakfast", url="ai-founders-breakfast",
                api_id="evt-123", guest_count=25, location_type="offline",
                start_at="2026-05-16T14:00:00Z"):
    return {
        "event": {
            "name": name,
            "url": url,
            "api_id": api_id,
            "start_at": start_at,
            "location_type": location_type,
        },
        "calendar": {"name": "Boston Founders Club", "description_short": ""},
        "ticket_info": {"require_approval": False},
        "hosts": [],
        "guest_count": guest_count,
    }

def test_normalize_returns_dict():
    result = normalize_luma_event(_make_entry())
    assert result is not None
    assert result["name"] == "AI Founders Breakfast"
    assert result["api_id"] == "evt-123"

def test_normalize_missing_name_returns_none():
    result = normalize_luma_event(_make_entry(name=""))
    assert result is None

def test_normalize_missing_url_returns_none():
    result = normalize_luma_event(_make_entry(url=""))
    assert result is None

def test_normalize_full_url_passthrough():
    result = normalize_luma_event(_make_entry(url="https://lu.ma/ai-breakfast"))
    assert result["url"] == "https://lu.ma/ai-breakfast"

def test_normalize_slug_prefixed():
    result = normalize_luma_event(_make_entry(url="ai-breakfast"))
    assert result["url"] == "https://lu.ma/ai-breakfast"

def test_normalize_rejects_bad_domain():
    result = normalize_luma_event(_make_entry(url="https://evil.com/event"))
    assert result is None

# ── pre_filter ────────────────────────────────────────────────────────────────

def _make_event(name="AI Founders Breakfast", guest_count=25,
                location_type="offline", url="https://lu.ma/ai-founders",
                start_at=None):
    if start_at is None:
        start_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    return {
        "name": name,
        "api_id": "evt-123",
        "start_at": start_at,
        "url": url,
        "source": "luma",
        "location_type": location_type,
        "organizer_name": "Boston Founders Club",
        "organizer_desc": "",
        "guest_count": guest_count,
        "require_approval": False,
        "verified": True,
    }

def _memory():
    return {"processed_contacts": [], "processed_events": []}

def _config(**overrides):
    cfg = {
        "window_days_attendees": 3,
        "extra_keyword_deny": [],
        "extra_keyword_allow": [],
    }
    cfg.update(overrides)
    return cfg

def test_prefilter_passes_good_event():
    result = pre_filter([_make_event()], _memory(), _config())
    assert len(result) == 1

def test_prefilter_drops_online():
    result = pre_filter([_make_event(location_type="online")], _memory(), _config())
    assert len(result) == 0

def test_prefilter_keeps_zero_guest_event():
    # guest_count filter removed — approval-required events always show 0
    result = pre_filter([_make_event(guest_count=0)], _memory(), _config())
    assert len(result) == 1

def test_prefilter_drops_lifestyle():
    result = pre_filter([_make_event(name="Saturday Yoga & Wellness")], _memory(), _config())
    assert len(result) == 0

def test_prefilter_drops_no_startup_keyword():
    # Use a neutral organizer so only the event name is checked for startup keywords
    event = _make_event(name="General Community Meetup")
    event["organizer_name"] = "Community Group"
    event["organizer_desc"] = ""
    result = pre_filter([event], _memory(), _config())
    assert len(result) == 0

def test_prefilter_drops_past_event():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    result = pre_filter([_make_event(start_at=past)], _memory(), _config())
    assert len(result) == 0

def test_prefilter_drops_out_of_window():
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    result = pre_filter([_make_event(start_at=future)], _memory(), _config())
    assert len(result) == 0

def test_prefilter_drops_seen_event():
    memory = {"processed_contacts": [], "processed_events": [
        {"url": "https://lu.ma/ai-founders", "date_seen": "2026-05-15"}
    ]}
    result = pre_filter([_make_event()], memory, _config())
    assert len(result) == 0

# ── fetch_event_hosts ─────────────────────────────────────────────────────────

def _make_raw_entry(event_name="AI Founders Hackathon", api_id="evt-abc",
                    hosts=None):
    if hosts is None:
        hosts = [
            {
                "name": "Jane Smith",
                "username": "janesmith",
                "bio_short": "Founder & CEO",
                "job_title": None,
                "company": None,
                "linkedin_handle": "/in/janesmith",
                "twitter_handle": None,
            }
        ]
    return {
        "event": {"api_id": api_id, "name": event_name, "url": api_id},
        "hosts": hosts,
    }

def test_fetch_hosts_basic():
    results = fetch_event_hosts([_make_raw_entry()])
    assert len(results) == 1
    h = results[0]
    assert h["name"] == "Jane Smith"
    assert h["linkedin"] == "https://www.linkedin.com/in/janesmith"
    assert h["linkedin_source"] == "luma_profile"
    assert h["event"] == "AI Founders Hackathon"
    assert not h["is_speaker"]

def test_fetch_hosts_skips_org_accounts():
    # Org names (ending with Corp, Inc, Network, etc.) are filtered out
    for org_name in ["Acme Corp", "Tech Network", "Boston Events Inc"]:
        entry = _make_raw_entry(hosts=[{
            "name": org_name,
            "username": None, "bio_short": None, "job_title": None,
            "company": None, "linkedin_handle": "/in/someone", "twitter_handle": None,
        }])
        results = fetch_event_hosts([entry])
        assert results == [], f"Expected {org_name} to be filtered as org account"

def test_fetch_hosts_skips_company_linkedin_handle():
    # Person name but company-style LinkedIn handle — person included, linkedin None
    entry = _make_raw_entry(hosts=[{
        "name": "Alice Brown",
        "username": None, "bio_short": None, "job_title": None,
        "company": None, "linkedin_handle": "/company/acme", "twitter_handle": None,
    }])
    results = fetch_event_hosts([entry])
    assert len(results) == 1
    assert results[0]["linkedin"] is None

def test_fetch_hosts_no_linkedin_handle():
    entry = _make_raw_entry(hosts=[{
        "name": "Bob Jones",
        "username": "bobjones",
        "bio_short": None,
        "job_title": None,
        "company": None,
        "linkedin_handle": None,
        "twitter_handle": None,
    }])
    results = fetch_event_hosts([entry])
    assert results[0]["linkedin"] is None
    assert results[0]["linkedin_source"] is None

def test_fetch_hosts_deduplicates_names():
    entries = [_make_raw_entry(api_id="evt-1"), _make_raw_entry(api_id="evt-2")]
    results = fetch_event_hosts(entries)
    # Same name "Jane Smith" across two events — only one returned
    assert len(results) == 1

def test_fetch_hosts_respects_max_per_event():
    hosts = [
        {"name": f"Person {i}", "username": None, "bio_short": None,
         "job_title": None, "company": None,
         "linkedin_handle": f"/in/person{i}", "twitter_handle": None}
        for i in range(10)
    ]
    results = fetch_event_hosts([_make_raw_entry(hosts=hosts)], max_per_event=3)
    assert len(results) == 3

def test_fetch_hosts_empty_entries():
    assert fetch_event_hosts([]) == []
