import pytest
from datetime import date, timedelta
from memory import (
    trim_memory, make_contact_key, is_contact_seen,
    mark_contact_seen, is_event_seen, mark_event_seen
)

def test_trim_removes_old_contacts():
    old_date = (date.today() - timedelta(days=31)).isoformat()
    memory = {
        "processed_contacts": [
            {"key": "linkedin.com/in/old", "name": "Old", "date_seen": old_date},
            {"key": "linkedin.com/in/new", "name": "New", "date_seen": date.today().isoformat()},
        ],
        "processed_events": []
    }
    result = trim_memory(memory, contact_expiry_days=30, event_expiry_days=7)
    keys = [c["key"] for c in result["processed_contacts"]]
    assert "linkedin.com/in/old" not in keys
    assert "linkedin.com/in/new" in keys

def test_trim_removes_old_events():
    old_date = (date.today() - timedelta(days=8)).isoformat()
    memory = {
        "processed_contacts": [],
        "processed_events": [
            {"url": "lu.ma/old-event", "date_seen": old_date},
            {"url": "lu.ma/new-event", "date_seen": date.today().isoformat()},
        ]
    }
    result = trim_memory(memory, contact_expiry_days=30, event_expiry_days=7)
    urls = [e["url"] for e in result["processed_events"]]
    assert "lu.ma/old-event" not in urls
    assert "lu.ma/new-event" in urls

def test_make_contact_key_prefers_linkedin():
    key = make_contact_key("https://linkedin.com/in/johnsmith", "John Smith", "Acme")
    assert key == "https://linkedin.com/in/johnsmith"

def test_make_contact_key_normalizes_linkedin():
    key = make_contact_key("https://linkedin.com/in/JohnSmith/", "John Smith", "Acme")
    assert key == "https://linkedin.com/in/johnsmith"

def test_make_contact_key_fallback_slug():
    key = make_contact_key(None, "John Smith", "Acme AI")
    assert key == "name:john-smith:acme-ai"

def test_is_contact_seen_true():
    memory = {"processed_contacts": [{"key": "linkedin.com/in/john", "name": "John", "date_seen": "2026-05-15"}]}
    assert is_contact_seen(memory, "linkedin.com/in/john") is True

def test_is_contact_seen_false():
    memory = {"processed_contacts": []}
    assert is_contact_seen(memory, "linkedin.com/in/john") is False

def test_mark_contact_seen():
    memory = {"processed_contacts": []}
    mark_contact_seen(memory, "linkedin.com/in/john", "John")
    assert is_contact_seen(memory, "linkedin.com/in/john") is True

def test_is_event_seen():
    memory = {"processed_events": [{"url": "lu.ma/test", "date_seen": "2026-05-15"}]}
    assert is_event_seen(memory, "lu.ma/test") is True
    assert is_event_seen(memory, "lu.ma/other") is False

def test_mark_event_seen():
    memory = {"processed_events": []}
    mark_event_seen(memory, "lu.ma/new-event")
    assert is_event_seen(memory, "lu.ma/new-event") is True

def test_make_contact_key_both_empty():
    key = make_contact_key(None, "", "")
    assert key == "name:unknown:unknown"
