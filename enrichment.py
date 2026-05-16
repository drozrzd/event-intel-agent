import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote

import requests

from security import validate_linkedin_url

# Module-level cache: company → result or None. Resets each process (i.e. each cron run).
_funding_cache: dict = {}

_AMOUNT_RE = re.compile(
    r'\$[\d,.]+\s*(?:M|K|B|million|billion|thousand)?',
    re.IGNORECASE,
)
_ROUND_RE = re.compile(
    r'\b(pre-?seed|seed\s+round|seed|series\s+[a-c]|angel\s+round|angel)\b',
    re.IGNORECASE,
)
_COMPANY_FROM_BIO_RE = re.compile(
    r'(?:@|\bat\b|\bwith\b)\s*([A-Z][A-Za-z0-9][A-Za-z0-9\s&.\-]{1,30}?)(?:\s*[,.|]|$)',
)


def _extract_company_from_bio(bio: str) -> str:
    """Pull company name from bios like 'Co-Founder @ Second Axis'."""
    if not bio:
        return ""
    m = _COMPANY_FROM_BIO_RE.search(bio)
    return m.group(1).strip() if m else ""


def check_recent_funding(company: str, days: int = 90) -> Optional[dict]:
    """
    Search Google News RSS for recent funding news about a company.
    Returns {"amount": "$2M", "round": "Seed", "headline": "...", "date": "2026-05-01"}
    or None if nothing found within `days` days.
    Results are cached per company for the lifetime of the process.
    """
    company = (company or "").strip()
    if not company or len(company) < 3:
        return None
    if company in _funding_cache:
        return _funding_cache[company]

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = f'"{company}" raised OR funding OR seed OR "series a" OR "series b" OR investment'
    rss_url = (
        f"https://news.google.com/rss/search?q={quote(query)}"
        f"&hl=en-US&gl=US&ceid=US:en"
    )

    try:
        resp = requests.get(
            rss_url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; event-intel/1.0)"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"[FUNDING] RSS fetch error for '{company}': {e}")
        _funding_cache[company] = None
        return None

    result = None
    for item in root.findall(".//item"):
        pub_date = item.findtext("pubDate", "")
        try:
            pub_dt = parsedate_to_datetime(pub_date)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt < cutoff:
                continue
        except Exception:
            continue

        title = item.findtext("title", "")
        description = item.findtext("description", "")
        text = f"{title} {description}"

        if company.lower() not in text.lower():
            continue

        amounts = _AMOUNT_RE.findall(text)
        round_match = _ROUND_RE.search(text)

        result = {
            "amount": amounts[0] if amounts else None,
            "round": round_match.group(1).title() if round_match else None,
            "headline": title[:120],
            "date": pub_dt.strftime("%Y-%m-%d"),
        }
        break  # first (most recent) matching article wins

    _funding_cache[company] = result
    return result


def enrich_funding(contact: dict) -> dict:
    """
    Check if the contact's company has recent funding news.
    If found: prepends a signal and bumps score by 2 (capped at 10).
    Mutates contact in place and returns it.
    """
    company = contact.get("company") or _extract_company_from_bio(contact.get("bio", ""))
    if not company:
        return contact

    funding = check_recent_funding(company)
    if not funding:
        return contact

    parts = []
    if funding.get("amount"):
        parts.append(funding["amount"])
    if funding.get("round"):
        parts.append(funding["round"])
    signal = "Recently raised " + " ".join(parts) if parts else "Recently funded"

    existing = [s for s in contact.get("signals", []) if "raised" not in s.lower()]
    contact["signals"] = [signal] + existing
    contact["score"] = min(10, int(contact.get("score") or 5) + 2)

    print(f"[FUNDING] {contact.get('name', '?')} @ {company}: {signal}")
    return contact


def linkedin_from_luma_profile(username: str) -> Optional[str]:
    """Visit lu.ma/u/{username}, return LinkedIn URL from social links or None."""
    if not username:
        return None
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"https://lu.ma/u/{username}", timeout=15000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            links = page.eval_on_selector_all(
                "a[href*='linkedin.com/in/']",
                "els => els.map(el => el.href)"
            )
            for link in links:
                if validate_linkedin_url(link):
                    return link.rstrip("/")
            return None
        except Exception as e:
            print(f"[ENRICH] Luma profile error {username}: {e}")
            return None
        finally:
            browser.close()


def linkedin_from_google(
    name: str, company: str, delay_seconds: int = 10
) -> Optional[str]:
    """
    Google site:linkedin.com search. Returns URL, "CAPTCHA" signal, or None.
    Caller must check for "CAPTCHA" string and count toward abort threshold.
    """
    if not name:
        return None
    query = (
        f'"{name}" "{company}" site:linkedin.com'
        if company
        else f'"{name}" boston site:linkedin.com'
    )
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        try:
            time.sleep(delay_seconds)
            page.goto(
                f"https://www.google.com/search?q={query}", timeout=15000
            )
            if "google.com/sorry" in page.url or "captcha" in page.content().lower()[:500]:
                print(f"[ENRICH] Google CAPTCHA detected for {name}")
                return "CAPTCHA"
            links = page.eval_on_selector_all(
                "a[href*='linkedin.com/in/']",
                "els => els.map(el => el.href)"
            )
            for link in links:
                clean = _clean_google_url(link)
                if clean and validate_linkedin_url(clean):
                    return clean.rstrip("/")
            return None
        except Exception as e:
            print(f"[ENRICH] Google search error for {name}: {e}")
            return None
        finally:
            browser.close()


def _clean_google_url(url: str) -> str:
    m = re.search(r"[?&]q=(https?://(?:www\.)?linkedin\.com/in/[^&]+)", url)
    if m:
        return m.group(1)
    if "linkedin.com/in/" in url and "google.com" not in url:
        return url
    return ""


def enrich_linkedin(attendee: dict, config: dict, captcha_count: list) -> dict:
    """
    Try Layer A (Luma profile) then Layer B (Google) for LinkedIn URL.
    captcha_count is a mutable [N] shared across calls to track abort threshold.
    Modifies attendee in place, returns it.
    """
    max_captcha = config.get("google_captcha_abort_threshold", 3)

    # Layer A: Luma profile page
    username = attendee.get("username", "")
    if username:
        url = linkedin_from_luma_profile(username)
        if url and validate_linkedin_url(url):
            attendee["linkedin"] = url
            attendee["linkedin_source"] = "luma_profile"
            return attendee

    # Layer B: Google (only if under CAPTCHA threshold)
    if captcha_count[0] >= max_captcha:
        return attendee

    result = linkedin_from_google(
        attendee.get("name", ""),
        attendee.get("company", ""),
        delay_seconds=config.get("google_search_delay_seconds", 10),
    )
    if result == "CAPTCHA":
        captcha_count[0] += 1
        print(f"[ENRICH] CAPTCHA count: {captcha_count[0]}/{max_captcha}")
    elif result and validate_linkedin_url(result):
        attendee["linkedin"] = result
        attendee["linkedin_source"] = "google_search"

    return attendee
