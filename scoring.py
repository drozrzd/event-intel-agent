import json
from llm import parse_json_response
from security import sanitize

_IMS_CONTEXT = """
Imaginary Space (IMS) builds MVPs for funded early-stage startups.
Target: pre-seed to Series A founders in AI, SaaS, or developer-facing products.
""".strip()

_SCORE_SYSTEM = f"""{_IMS_CONTEXT}

Score each person for ICP fit. Input is a JSON array of attendees.
Return a JSON array:
[{{"index": 0, "score": 8, "signals": ["Seed-stage AI founder", "hiring engineers"]}}]

Score 7-10: founder/co-founder/CTO title, pre-seed to Series A,
  AI/SaaS/dev product, hiring engineers, recently raised.
Score 4-6: general tech employee, product manager, unclear stage.
Score 1-3: agency owner, solo consultant, no technical product,
  non-technical role, Series B+ employee who is not a founder.

Rules:
- If title/company/bio are all empty, score 5 (unknown, not bad).
- signals: exactly 2 phrases, max 40 characters each.
- NEVER invent funding data not present in the input.
- Return ONLY the JSON array, no other text."""


def score_attendees(attendees: list, call_groq_fn) -> list:
    if not attendees:
        return []
    input_data = [
        {
            "index": i,
            "name": a.get("name", ""),
            "title": a.get("title", ""),
            "company": a.get("company", ""),
            "bio": a.get("bio", ""),
            "context": " | ".join(a.get("signals", [])),
        }
        for i, a in enumerate(attendees)
    ]
    raw = call_groq_fn(_SCORE_SYSTEM, json.dumps(input_data))
    results = _parse_scores(raw, len(attendees))

    for item in results:
        idx = item.get("index")
        if isinstance(idx, int) and 0 <= idx < len(attendees):
            attendees[idx]["score"] = int(item.get("score", 5))
            attendees[idx]["signals"] = [
                sanitize(s, max_chars=40)
                for s in (item.get("signals") or [])[:2]
            ]
    return attendees


def _parse_scores(raw: str, count: int) -> list:
    result = parse_json_response(raw, fallback=None)
    if isinstance(result, list):
        return result
    print(f"[SCORE] Fallback scores for {count} attendees")
    return [{"index": i, "score": 5, "signals": ["parse error"]} for i in range(count)]
