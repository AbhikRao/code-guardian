# Code Guardian

**Autonomous self-healing code review pipeline — AMD Developer Hackathon 2026**

Code Guardian scans a GitHub repository for bugs, automatically patches them, writes tests, runs them in a sandbox, and opens a ready-to-merge pull request. All powered by Llama 3.3 70B on AMD Instinct MI300X.

## Pipeline

```
SCANNER → FIXER → TEST WRITER → EXECUTOR → REPORTER
```

1. **Scanner** — Clones repo, runs pyflakes + LLM to find bugs and vulnerabilities
2. **Fixer** — Generates patched versions of affected files
3. **Test Writer** — Writes assertion-driven pytest tests for each fix
4. **Executor** — Runs tests in an isolated sandbox (Docker preferred, subprocess fallback)
5. **Reporter** — Opens a GitHub Pull Request with diffs and test results

## Setup

```bash
git clone https://github.com/AbhikRao/code-guardian
cd code-guardian
python3.13 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in AMD_API_KEY, AMD_BASE_URL, GITHUB_TOKEN
```

## Run

```bash
# Dashboard
streamlit run ui/dashboard.py

# CLI
python -m core.orchestrator https://github.com/user/repo
```

## LLM Fallback Chain

1. AMD Developer Cloud (MI300X, primary)
2. Groq (development/testing)
3. Ollama local (offline fallback)

## Tech Stack

- **LLM**: Llama 3.3 70B via AMD Developer Cloud (ROCm)
- **Framework**: LangChain agents + CrewAI
- **UI**: Streamlit
- **Testing**: pytest + pytest-timeout
- **GitHub**: PyGithub

## Environment Variables

```
AMD_API_KEY=          # AMD cloud or Groq key
AMD_BASE_URL=         # https://api.groq.com/openai/v1 or AMD endpoint
AMD_MODEL_NAME=       # llama-3.3-70b-versatile
GITHUB_TOKEN=         # Personal access token with repo scope
GITHUB_USERNAME=      # Your GitHub username
```

Built by [@AbhikRao](https://github.com/AbhikRao) for the AMD Developer Hackathon 2026 hosted on lablab.ai.
