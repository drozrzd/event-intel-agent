from enrichment import _clean_google_url
from security import validate_linkedin_url


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
