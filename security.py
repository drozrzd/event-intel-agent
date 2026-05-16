import re

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"forget\s+(all\s+)?(previous|prior|above|your)\s+instructions",
    r"you\s+are\s+now\s+(a\s+)?(new|different)",
    r"system\s*prompt\s*:",
    r"override\s+(all\s+)?instructions",
    r"jailbreak",
    r"do\s+not\s+follow",
    r"disregard\s+(all\s+)?(previous|prior)",
    r"new\s+instructions?\s*:",
    r"<\s*system\s*>",
    r"prompt\s*injection",
]

LINKEDIN_PATTERN = re.compile(
    r"^https?://(www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+/?$"
)


def sanitize(text: str, max_chars: int = 500) -> str:
    if not text:
        return ""
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, str(text), re.IGNORECASE):
            return "[REDACTED]"
    return str(text)[:max_chars]


def validate_linkedin_url(url: str) -> bool:
    if not url:
        return False
    return bool(LINKEDIN_PATTERN.match(url.strip()))
