import json
import re
import os

import requests
from groq import Groq

_groq_client = None


def _get_groq() -> Groq:
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


def _call_ollama(system: str, user: str) -> str:
    model = os.environ.get("OLLAMA_MODEL", "llama3.1")
    resp = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def call_groq(system: str, user: str) -> str:
    try:
        client = _get_groq()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[LLM] Groq failed ({e}), falling back to Ollama...")
        return _call_ollama(system, user)


def parse_json_response(text: str, fallback):
    try:
        cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
        match = re.search(r"[\[{]", cleaned)
        if match:
            cleaned = cleaned[match.start():]
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        print(f"[LLM] Failed to parse JSON: {text[:200]}")
        return fallback
