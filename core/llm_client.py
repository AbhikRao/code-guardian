"""
llm_client.py
─────────────
LLM connection with automatic 3-tier fallback:
  1. AMD Developer Cloud  (primary — MI300X, for demo)
  2. Groq                 (development / testing)
  3. Ollama local         (emergency fallback — runs quantized Llama locally)

The active provider is determined at startup by trying each in order.
Set FORCE_PROVIDER=amd|groq|ollama in .env to override.
"""

import os
import subprocess
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

AMD_API_KEY    = os.getenv("AMD_API_KEY", "")
AMD_BASE_URL   = os.getenv("AMD_BASE_URL", "https://api.groq.com/openai/v1")
AMD_MODEL_NAME = os.getenv("AMD_MODEL_NAME", "llama-3.3-70b-versatile")
FORCE_PROVIDER = os.getenv("FORCE_PROVIDER", "")   # pin to one provider if set

# Ollama config (local ROCm fallback)
OLLAMA_BASE_URL   = "http://localhost:11434/v1"
OLLAMA_MODEL_NAME = "llama3.1:8b"    # smaller model, runs on CPU or ROCm
OLLAMA_API_KEY    = "ollama"          # Ollama accepts any non-empty string

_client: OpenAI | None = None
_active_provider: str  = ""


def _ollama_running() -> bool:
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "http://localhost:11434/api/tags"],
            capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip() == "200"
    except Exception:
        return False


def _make_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


def get_client() -> tuple[OpenAI, str]:
    """Return (client, provider_name), trying each tier in order."""
    global _client, _active_provider

    if _client is not None:
        return _client, _active_provider

    if FORCE_PROVIDER == "ollama" or (not AMD_API_KEY and _ollama_running()):
        _client          = _make_client(OLLAMA_BASE_URL, OLLAMA_API_KEY)
        _active_provider = "ollama"
        print(f"[LLM] Using local Ollama ({OLLAMA_MODEL_NAME})")
        return _client, _active_provider

    if AMD_API_KEY:
        _client          = _make_client(AMD_BASE_URL, AMD_API_KEY)
        _active_provider = "groq/amd"
        return _client, _active_provider

    if _ollama_running():
        _client          = _make_client(OLLAMA_BASE_URL, OLLAMA_API_KEY)
        _active_provider = "ollama"
        print("[LLM] Cloud unavailable — falling back to local Ollama")
        return _client, _active_provider

    raise EnvironmentError(
        "No LLM provider available.\n"
        "  Option 1: Set AMD_API_KEY in .env (Groq or AMD cloud)\n"
        "  Option 2: Run Ollama locally:  ollama serve && ollama pull llama3.1:8b"
    )


def _active_model() -> str:
    _, provider = get_client()
    return OLLAMA_MODEL_NAME if provider == "ollama" else AMD_MODEL_NAME


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    client, _ = get_client()
    response  = client.chat.completions.create(
        model=_active_model(),
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )
    return response.choices[0].message.content.strip()


def chat_json(system_prompt: str, user_prompt: str) -> str:
    json_system = (
        system_prompt
        + "\n\nIMPORTANT: Reply ONLY with valid JSON. "
          "No markdown fences, no explanation, no extra text."
    )
    return chat(json_system, user_prompt, temperature=0.1)


if __name__ == "__main__":
    print("Testing LLM connection...")
    reply = chat(
        "You are a helpful assistant.",
        "Reply with exactly: 'AMD cloud connection successful.'"
    )
    print(reply)
