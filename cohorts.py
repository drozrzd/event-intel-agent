"""
Fetch founder contacts from public batch/cohort databases.
Current sources: YC batch launches (HN Algolia + YC company pages).
"""
import re
import time
import requests

from security import sanitize

MAX_NAME_CHARS = 100
MAX_BIO_CHARS = 150

_LAUNCH_RE = re.compile(r"Launch HN:\s*(.+?)\s*\(YC ([A-Z]\d+)\)")
_FOUNDED_RE = re.compile(
    r"Founded in \d{4} by ([^.]+?)(?:,\s*\w[\w\s]+ has|\.\s|$)", re.I
)
_LOCATION_RE = re.compile(r"based in ([^.]+)", re.I)

# Cities that are definitively NOT Boston — skip YC companies based there
_NON_BOSTON_CITIES = {
    "san francisco", "new york", "new york city", "nyc", "los angeles",
    "seattle", "austin", "chicago", "denver", "miami", "atlanta",
    "toronto", "london", "berlin", "singapore", "bangalore", "delhi",
    "mumbai", "tel aviv", "paris", "amsterdam",
}


def _yc_slug(company: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "", company.lower().replace(" ", "-")).strip("-")


def _fetch_yc_page(slug: str) -> dict:
    url = f"https://www.ycombinator.com/companies/{slug}"
    try:
        r = requests.get(
            url, timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; event-intel/1.0)"}
        )
        if r.status_code != 200:
            return {}
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.content, "html.parser")
        og_desc = soup.find("meta", property="og:description")
        return {"desc": og_desc.get("content", "") if og_desc else ""}
    except Exception as e:
        print(f"[YC] Fetch error {slug}: {e}")
        return {}


def _parse_founders(desc: str) -> list:
    m = _FOUNDED_RE.search(desc)
    if not m:
        return []
    raw = m.group(1)
    # Split on " and " and commas between capitalized names
    parts = re.split(r",\s*and\s*|(?<![A-Z])\band\b(?![A-Za-z])|,\s*(?=[A-Z])", raw)
    return [p.strip() for p in parts if p.strip()]


def _is_person_name(name: str) -> bool:
    """True if name looks like a real person (has space, no digits, reasonable length)."""
    if not name or " " not in name:
        return False
    if any(c.isdigit() for c in name):
        return False
    if len(name) > 50:
        return False
    return True


def fetch_yc_founders(config: dict) -> list:
    """
    Fetch YC batch founders via HN Algolia + YC company og:description.
    No city filter — YC founders are high ICP signal regardless of location.
    Each company contributes up to 2 founder contacts.
    """
    batches = config.get("yc_batches", ["W26", "S25"])
    seen_companies: set = set()
    contacts = []

    for batch in batches:
        try:
            r = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params={
                    "query": f"Launch HN YC {batch}",
                    "tags": "story",
                    "hitsPerPage": 100,
                },
                timeout=15,
            )
            hits = r.json().get("hits", [])
        except Exception as e:
            print(f"[YC] HN fetch error {batch}: {e}")
            continue

        print(f"[YC] {len(hits)} Launch HN posts for {batch}")

        for hit in hits:
            title = hit.get("title", "")
            m = _LAUNCH_RE.search(title)
            if not m or m.group(2) != batch:
                continue

            company = sanitize(m.group(1).strip(), max_chars=80)
            if not company or company in seen_companies:
                continue
            seen_companies.add(company)

            # Try primary slug then hyphenated fallback
            slug = _yc_slug(company)
            page = _fetch_yc_page(slug)
            if not page.get("desc"):
                alt = company.lower().replace(" ", "-")
                page = _fetch_yc_page(alt)
            time.sleep(0.3)

            desc = page.get("desc", "")
            location = ""
            loc_m = _LOCATION_RE.search(desc)
            if loc_m:
                location = loc_m.group(1).strip().rstrip(".")

            # Skip companies explicitly based in non-Boston cities
            loc_lower = location.lower()
            if any(city in loc_lower for city in _NON_BOSTON_CITIES):
                continue

            founder_names = _parse_founders(desc)
            if not founder_names:
                # Fall back to HN post author (typically a founder)
                author = hit.get("author", "")
                if author:
                    founder_names = [author]

            # Short description from HN title
            desc_m = re.search(r"[–\-]\s*(.+)$", title)
            short_desc = sanitize(
                desc_m.group(1).strip() if desc_m else "", max_chars=MAX_BIO_CHARS
            )

            signal = f"YC {batch} founder"
            if location and len(location) < 40:
                signal += f" · {location}"

            for fname in founder_names[:2]:
                name = sanitize(fname, max_chars=MAX_NAME_CHARS)
                if not _is_person_name(name):
                    continue
                contacts.append({
                    "name": name,
                    "company": company,
                    "bio": short_desc,
                    "linkedin": None,
                    "score": 7,
                    "signals": [signal],
                    "source": "yc",
                    "is_speaker": False,
                    "username": "",
                    "title": "Founder",
                })

    print(f"[YC] {len(contacts)} founders from {len(seen_companies)} YC companies")
    return contacts
