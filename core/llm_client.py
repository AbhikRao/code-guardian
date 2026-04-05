"""
llm_client.py
─────────────
LLM connection with 3-tier fallback:
  1. AMD Developer Cloud (primary — MI300X)
  2. Groq               (dev/testing)
  3. Ollama local       (offline fallback)

Reads config from environment variables or Streamlit secrets.
"""

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

def _get(key: str, default: str = "") -> str:
    """Read from env, then Streamlit secrets if running in cloud."""
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


def _ollama_running() -> bool:
    try:
        import subprocess
        r = subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}",
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


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    client, _ = get_client()
    resp = client.chat.completions.create(
        model=_model(), temperature=temperature,
        messages=[{"role":"system","content":system_prompt},
                  {"role":"user","content":user_prompt}],
    )
    return resp.choices[0].message.content.strip()


def chat_json(system_prompt: str, user_prompt: str) -> str:
    return chat(
        system_prompt + "\n\nIMPORTANT: Reply ONLY with valid JSON. No markdown, no extra text.",
        user_prompt, temperature=0.1
    )


if __name__ == "__main__":
    print("Testing LLM connection...")
    print(chat("You are helpful.", "Reply with exactly: 'AMD cloud connection successful.'"))
