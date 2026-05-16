import re
from bs4 import BeautifulSoup

from security import sanitize

MAX_NAME = 100
MAX_TITLE = 80
MAX_COMPANY = 80
MAX_BIO = 150
MAX_SPEAKERS_PER_VENUE = 8

# CSS class fragments that indicate a speaker bio card
_SPEAKER_CLASSES = re.compile(
    r"speaker|presenter|panelist|keynote|team-member|bio|profile|faculty",
    re.IGNORECASE,
)
# Heading tags that commonly hold speaker names
_HEADING_TAGS = {"h2", "h3", "h4", "h5"}
# Name-like: First Last — both parts ≥ 4 chars (filters "The Ion", "New Al", etc.)
_NAME_PATTERN = re.compile(r"\b([A-Z][a-z]{3,}\s[A-Z][a-z]{3,})\b")
# Common non-name words that satisfy the pattern but aren't person names
_NON_NAME_WORDS = {
    # Places / geography
    "Boston", "Cambridge", "Berkeley", "Downtown", "Beacon", "Hill",
    "Kendall", "Square", "Michigan", "Central", "North", "South", "East",
    "West", "Greater", "United", "States", "America", "England",
    # Organizations / categories
    "Greentown", "Innovation", "Labs", "Challenge", "District", "Harvard",
    "Featured", "Events", "Network", "Community", "Program", "Forum",
    "Press", "Media", "Newsletter",
    # Technology / industry terms
    "Deep", "Tech", "Energy", "Sector", "Technip", "Energies", "Next",
    "Clean", "Climate", "Carbon",
    # Action words / website UI
    "About", "Learn", "Register", "Contact", "Welcome", "Subscribe",
    "Upcoming", "Recent", "View", "Read", "Sign", "More", "Both",
    # Social / event words
    "Pickleball", "Social", "Year", "Anniversary", "Summit", "Locations",
    # Website navigation / UI elements
    "Venue", "Event", "Information", "Navigation", "Sidebar", "Header",
    "Footer", "Search", "Filter", "Category", "Featured", "Archive",
}
# Full phrase false positives
_FALSE_POSITIVES = {
    "The Engine", "Mass Challenge", "District Hall", "Harvard Innovation",
    "Greentown Labs", "View All", "Learn More", "Read More", "Sign Up",
    "Register Now", "Join Us", "Contact Us", "About Us", "New England",
    "United States", "Greater Boston", "North America", "South End",
    "Back Bay", "Kendall Square", "Featured Events", "Innovation Labs",
}


MAX_EVENT_LINKS_PER_VENUE = 5

# Link text/URL patterns that indicate an individual event page (not nav/UI)
_EVENT_LINK_PATTERN = re.compile(
    r"(event|summit|conference|hackathon|demo|pitch|accelerator|cohort|meetup|forum)",
    re.IGNORECASE,
)
# Paths to skip (pagination, tags, category pages, anchors)
_SKIP_PATH_PATTERN = re.compile(r"[?#]|/tag/|/category/|/page/", re.IGNORECASE)


def scrape_venue_speakers(config: dict) -> list:
    speakers = []

    for venue_url in config.get("venue_sites", []):
        try:
            venue_speakers = _scrape_one_venue(venue_url)
            speakers.extend(venue_speakers)
            print(f"[VENUES] {len(venue_speakers)} speakers from {venue_url}")
        except Exception as e:
            print(f"[VENUES] Error scraping {venue_url}: {e}")

    return speakers


def _scrape_one_venue(venue_url: str) -> list:
    from playwright.sync_api import sync_playwright
    from urllib.parse import urljoin, urlparse

    base_domain = urlparse(venue_url).netloc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(venue_url, timeout=20000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            listing_html = page.content()

            # Find event detail page links from the listing
            event_links = _find_event_links(listing_html, venue_url, base_domain)

            # Try the listing page itself first
            speakers = _extract_speakers_from_html(listing_html, venue_url)

            # Then follow event detail links
            for link in event_links[:MAX_EVENT_LINKS_PER_VENUE]:
                if len(speakers) >= MAX_SPEAKERS_PER_VENUE:
                    break
                try:
                    page.goto(link, timeout=15000)
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    detail_html = page.content()
                    found = _extract_speakers_from_html(detail_html, venue_url)
                    for s in found:
                        if s["name"] not in {x["name"] for x in speakers}:
                            speakers.append(s)
                    if found:
                        print(f"[VENUES]   +{len(found)} speakers from {link}")
                except Exception:
                    pass  # skip slow/broken detail pages silently

        except Exception as e:
            print(f"[VENUES] Playwright timeout on {venue_url}: {e}")
            return []
        finally:
            browser.close()

    return speakers[:MAX_SPEAKERS_PER_VENUE]


def _find_event_links(html: str, base_url: str, base_domain: str) -> list:
    from urllib.parse import urljoin, urlparse
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        # Same domain only, no skip-patterns, looks like an event page
        if (parsed.netloc == base_domain
                and full not in seen
                and not _SKIP_PATH_PATTERN.search(full)
                and (_EVENT_LINK_PATTERN.search(full) or _EVENT_LINK_PATTERN.search(a.get_text()))):
            seen.add(full)
            links.append(full)
    return links


def _extract_speakers_from_html(html: str, source_url: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    speakers = []
    seen_names = set()

    # Strategy 1: find elements whose CSS class signals a speaker card
    for el in soup.find_all(True):
        classes = " ".join(el.get("class", []))
        if not _SPEAKER_CLASSES.search(classes):
            continue
        text = el.get_text(separator=" ", strip=True)
        if len(text) < 10 or len(text) > 800:
            continue
        _extract_names_from_block(text, source_url, seen_names, speakers)
        if len(speakers) >= MAX_SPEAKERS_PER_VENUE:
            break

    # Strategy 2: short headings (≤ 50 chars) that appear inside a speaker section
    # Only used if Strategy 1 found nothing AND heading text is very short (name-only)
    if not speakers:
        for section in soup.find_all(["section", "article"]):
            section_text = section.get_text(separator=" ", strip=True).lower()
            if not re.search(r"\b(speaker|presenter|panelist|keynote)\b", section_text):
                continue
            for heading in section.find_all(_HEADING_TAGS):
                text = heading.get_text(separator=" ", strip=True)
                # Only very short headings are likely to be person names
                if 5 <= len(text) <= 50:
                    _extract_names_from_block(text, source_url, seen_names, speakers)
            if len(speakers) >= MAX_SPEAKERS_PER_VENUE:
                break

    return speakers[:MAX_SPEAKERS_PER_VENUE]


def _extract_names_from_block(
    text: str, source_url: str, seen_names: set, speakers: list
) -> None:
    for match in _NAME_PATTERN.finditer(text):
        candidate = match.group(1).strip()
        if candidate in _FALSE_POSITIVES or candidate in seen_names:
            continue
        parts = candidate.split()
        if len(parts) < 2 or any(p in _NON_NAME_WORDS for p in parts):
            continue

        title, company = _extract_title_company(text, candidate)

        # Reject if title looks like an event description, not a job title
        _EVENT_TITLE_PATTERN = re.compile(
            r"\b(pitch|hackathon|fair|summit|conference|demo|innovation|"
            r"season|festival|bootcamp|forum)\b|[@&/]|\bwith\s",
            re.IGNORECASE,
        )
        if title and _EVENT_TITLE_PATTERN.search(title):
            continue

        seen_names.add(candidate)
        speakers.append({
            "name": sanitize(candidate, max_chars=MAX_NAME),
            "username": "",
            "title": sanitize(title, max_chars=MAX_TITLE),
            "company": sanitize(company, max_chars=MAX_COMPANY),
            "bio": sanitize(text[:MAX_BIO], max_chars=MAX_BIO),
            "linkedin": None,
            "linkedin_source": None,
            "score": 9,
            "signals": ["Speaker at top Boston venue", f"via {_venue_name(source_url)}"],
            "event": _venue_name(source_url),
            "is_speaker": True,
        })


def _extract_title_company(text: str, name: str) -> tuple:
    """Best-effort title/company extraction from surrounding text block."""
    m = re.search(
        re.escape(name) + r"[,\s|–-]+([^,\n]+(?:,\s*[^,\n]+)?)",
        text,
    )
    if m:
        parts = m.group(1).split(",")
        title = parts[0].strip()[:MAX_TITLE] if parts else ""
        company = parts[1].strip()[:MAX_COMPANY] if len(parts) > 1 else ""
        return title, company
    return "", ""


def _venue_name(url: str) -> str:
    mapping = {
        "cic.us": "CIC Boston",
        "masschallenge.org": "MassChallenge",
        "engine.xyz": "The Engine",
        "districthall": "District Hall",
        "greentownlabs": "Greentown Labs",
        "innovationlabs.harvard": "Harvard iLab",
    }
    for key, name in mapping.items():
        if key in url:
            return name
    return url.split("/")[2]
