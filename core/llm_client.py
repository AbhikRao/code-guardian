"""
llm_client.py
─────────────
LLM connection with 3-tier fallback + automatic rate-limit retry.

Tiers:
  1. AMD Developer Cloud (primary — MI300X)
  2. Groq               (dev/testing)
  3. Ollama local       (offline fallback)

Rate limit handling:
  On 429 the error message includes "Please try again in Xs".
  We parse that wait time and sleep before retrying (max 3 retries).
  This means the pipeline self-heals instead of crashing on rate limits.
"""

import os
import re
import time
from openai import OpenAI, RateLimitError
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    val = os.getenv(key, "")
    if not val:
        try:
            import streamlit as st
            val = st.secrets.get(key, default)
        except Exception:
            val = default
    return val


AMD_API_KEY    = _get("AMD_API_KEY")
AMD_BASE_URL   = _get("AMD_BASE_URL",   "https://api.groq.com/openai/v1")
AMD_MODEL_NAME = _get("AMD_MODEL_NAME", "llama-3.3-70b-versatile")
OLLAMA_URL     = "http://localhost:11434/v1"
OLLAMA_MODEL   = "llama3.1:8b"

_client:   OpenAI | None = None
_provider: str           = ""

MAX_RETRIES = 3


def _ollama_running() -> bool:
    try:
        import subprocess
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "http://localhost:11434/api/tags"],
            capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == "200"
    except Exception:
        return False


def get_client() -> tuple[OpenAI, str]:
    global _client, _provider
    if _client:
        return _client, _provider

    if AMD_API_KEY:
        _client   = OpenAI(api_key=AMD_API_KEY, base_url=AMD_BASE_URL)
        _provider = "cloud"
        return _client, _provider

    if _ollama_running():
        _client   = OpenAI(api_key="ollama", base_url=OLLAMA_URL)
        _provider = "ollama"
        print("[LLM] Using local Ollama")
        return _client, _provider

    raise EnvironmentError(
        "No LLM provider available. Set AMD_API_KEY in .env or Streamlit secrets."
    )


def _model() -> str:
    _, p = get_client()
    return OLLAMA_MODEL if p == "ollama" else AMD_MODEL_NAME


def _parse_retry_seconds(error_msg: str) -> float:
    """Extract wait time from Groq/AMD rate limit error messages."""
    # Matches: "Please try again in 9m45.79s" or "try again in 30s"
    m = re.search(r'try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s', str(error_msg))
    if m:
        minutes = float(m.group(1) or 0)
        seconds = float(m.group(2) or 0)
        return minutes * 60 + seconds
    return 60.0  # safe default


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    client, _ = get_client()
    last_err = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=_model(), temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
            )
            return resp.choices[0].message.content.strip()

        except RateLimitError as e:
            last_err = e
            wait = _parse_retry_seconds(str(e))
            # Cap the wait to 90s so the UI doesn't appear frozen forever
            wait = min(wait, 90.0)
            print(f"[LLM] Rate limit hit. Waiting {wait:.0f}s (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(wait)

        except Exception as e:
            raise e

    raise last_err


def chat_json(system_prompt: str, user_prompt: str) -> str:
    return chat(
        system_prompt + "\n\nIMPORTANT: Reply ONLY with valid JSON. "
                        "No markdown fences, no explanation, no extra text.",
        user_prompt, temperature=0.1
    )


if __name__ == "__main__":
    print("Testing LLM connection...")
    print(chat("You are helpful.", "Reply with exactly: 'AMD cloud connection successful.'"))
