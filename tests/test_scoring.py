import pytest
from scoring import score_attendees, _parse_scores


def test_parse_scores_happy_path():
    raw = '[{"index": 0, "score": 8, "signals": ["Seed founder", "hiring engineers"]}]'
    result = _parse_scores(raw, count=1)
    assert result[0]["score"] == 8
    assert result[0]["signals"] == ["Seed founder", "hiring engineers"]


def test_parse_scores_handles_markdown_wrapper():
    raw = '```json\n[{"index": 0, "score": 7, "signals": ["AI startup"]}]\n```'
    result = _parse_scores(raw, count=1)
    assert result[0]["score"] == 7


def test_parse_scores_bad_json_returns_fallback():
    result = _parse_scores("not json at all", count=2)
    assert len(result) == 2
    assert result[0]["score"] == 5
    assert result[1]["score"] == 5


def test_score_attendees_applies_scores():
    attendees = [
        {"name": "John", "title": "CEO", "company": "Acme AI", "bio": "Building ML infra"},
        {"name": "Jane", "title": "Barista", "company": "Coffee Shop", "bio": ""},
    ]

    def mock_groq(system, user):
        return '[{"index": 0, "score": 9, "signals": ["AI founder", "ML product"]}, {"index": 1, "score": 2, "signals": ["No startup fit", "non-tech role"]}]'

    result = score_attendees(attendees, mock_groq)
    assert result[0]["score"] == 9
    assert result[1]["score"] == 2
    assert "AI founder" in result[0]["signals"]


def test_score_attendees_caps_signal_length():
    attendees = [{"name": "John", "title": "CEO", "company": "Acme", "bio": ""}]

    def mock_groq(system, user):
        return '[{"index": 0, "score": 7, "signals": ["' + "x" * 100 + '"]}]'

    result = score_attendees(attendees, mock_groq)
    assert len(result[0]["signals"][0]) <= 40
