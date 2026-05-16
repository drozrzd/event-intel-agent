import re
import time
from typing import Optional

from security import validate_linkedin_url


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
