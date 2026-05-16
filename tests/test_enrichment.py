import enrichment
from enrichment import _clean_google_url, _extract_company_from_bio, enrich_funding
from security import validate_linkedin_url

# ── RSS XML fixture ────────────────────────────────────────────────────────────
_RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>{title}</title>
      <description>{desc}</description>
      <pubDate>{pub}</pubDate>
    </item>
  </channel>
</rss>"""

_RECENT_DATE = "Fri, 01 May 2026 12:00:00 +0000"
_OLD_DATE    = "Mon, 01 Jan 2024 12:00:00 +0000"


def test_clean_google_url_extracts_linkedin():
    wrapped = "https://www.google.com/url?q=https://linkedin.com/in/johnsmith&sa=U"
    assert _clean_google_url(wrapped) == "https://linkedin.com/in/johnsmith"


def test_clean_google_url_passthrough():
    url = "https://linkedin.com/in/johnsmith"
    assert _clean_google_url(url) == url


def test_clean_google_url_non_linkedin():
    assert _clean_google_url("https://google.com/search") == ""


def test_enrich_linkedin_skips_when_captcha_threshold_hit(mocker):
    mocker.patch("enrichment.linkedin_from_luma_profile", return_value=None)
    google_mock = mocker.patch("enrichment.linkedin_from_google", return_value="CAPTCHA")
    from enrichment import enrich_linkedin
    captcha_count = [3]  # already at threshold
    config = {"google_captcha_abort_threshold": 3, "google_search_delay_seconds": 0}
    attendee = {"username": "", "name": "John", "company": "Acme", "linkedin": None}
    result = enrich_linkedin(attendee, config, captcha_count)
    google_mock.assert_not_called()
    assert result["linkedin"] is None

# ── _extract_company_from_bio ──────────────────────────────────────────────────

def test_extract_company_at_symbol():
    assert _extract_company_from_bio("Co-Founder @ Second Axis") == "Second Axis"

def test_extract_company_at_word():
    assert _extract_company_from_bio("CEO at Acme Corp") == "Acme Corp"

def test_extract_company_empty_bio():
    assert _extract_company_from_bio("") == ""

def test_extract_company_no_match():
    assert _extract_company_from_bio("loves coffee and startups") == ""

# ── check_recent_funding ───────────────────────────────────────────────────────

def _mock_rss(mocker, title, desc, pub):
    xml = _RSS_TEMPLATE.format(title=title, desc=desc, pub=pub)
    mock_resp = mocker.MagicMock()
    mock_resp.content = xml.encode()
    mock_resp.raise_for_status = lambda: None
    mocker.patch("enrichment.requests.get", return_value=mock_resp)
    enrichment._funding_cache.clear()

def test_check_recent_funding_finds_amount(mocker):
    _mock_rss(mocker,
        title="Second Axis raises $2M seed round",
        desc="Second Axis announced today...",
        pub=_RECENT_DATE)
    from enrichment import check_recent_funding
    result = check_recent_funding("Second Axis")
    assert result is not None
    assert "$2M" in result["amount"]
    assert result["round"] is not None

def test_check_recent_funding_ignores_old_news(mocker):
    _mock_rss(mocker,
        title="Second Axis raises $2M",
        desc="Second Axis announced...",
        pub=_OLD_DATE)
    enrichment._funding_cache.clear()
    from enrichment import check_recent_funding
    result = check_recent_funding("Second Axis")
    assert result is None

def test_check_recent_funding_skips_unrelated_article(mocker):
    _mock_rss(mocker,
        title="TechCorp raises $5M series A",
        desc="TechCorp announced funding",
        pub=_RECENT_DATE)
    enrichment._funding_cache.clear()
    from enrichment import check_recent_funding
    result = check_recent_funding("Second Axis")
    assert result is None

def test_check_recent_funding_caches_result(mocker):
    _mock_rss(mocker,
        title="Second Axis raises $1M",
        desc="Second Axis ...",
        pub=_RECENT_DATE)
    from enrichment import check_recent_funding
    enrichment._funding_cache.clear()
    check_recent_funding("Second Axis")
    get_mock = mocker.patch("enrichment.requests.get")
    check_recent_funding("Second Axis")  # second call — should use cache
    get_mock.assert_not_called()

def test_check_recent_funding_empty_company():
    from enrichment import check_recent_funding
    assert check_recent_funding("") is None
    assert check_recent_funding("  ") is None

# ── enrich_funding ─────────────────────────────────────────────────────────────

def test_enrich_funding_adds_signal_and_boosts_score(mocker):
    mocker.patch("enrichment.check_recent_funding", return_value={
        "amount": "$2M", "round": "Seed", "headline": "X raises $2M", "date": "2026-05-01"
    })
    contact = {"name": "Kabir", "company": "Second Axis", "score": 6, "signals": ["Organizer of Hackathon"]}
    result = enrich_funding(contact)
    assert any("raised" in s.lower() for s in result["signals"])
    assert result["score"] == 8

def test_enrich_funding_score_capped_at_10(mocker):
    mocker.patch("enrichment.check_recent_funding", return_value={
        "amount": "$5M", "round": "Series A", "headline": "...", "date": "2026-05-01"
    })
    contact = {"name": "Alice", "company": "Acme", "score": 9, "signals": []}
    result = enrich_funding(contact)
    assert result["score"] == 10

def test_enrich_funding_falls_back_to_bio(mocker):
    mock = mocker.patch("enrichment.check_recent_funding", return_value=None)
    contact = {"name": "Bob", "company": "", "bio": "CEO @ Acme Inc", "score": 5, "signals": []}
    enrich_funding(contact)
    mock.assert_called_once_with("Acme Inc")

def test_enrich_funding_no_company_skips(mocker):
    mock = mocker.patch("enrichment.check_recent_funding")
    contact = {"name": "Bob", "company": "", "bio": "", "score": 5, "signals": []}
    enrich_funding(contact)
    mock.assert_not_called()

def test_enrich_funding_no_result_unchanged(mocker):
    mocker.patch("enrichment.check_recent_funding", return_value=None)
    contact = {"name": "Bob", "company": "Acme", "score": 5, "signals": ["some signal"]}
    result = enrich_funding(contact)
    assert result["score"] == 5
    assert result["signals"] == ["some signal"]
