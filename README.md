# Code Guardian

**Autonomous self-healing code review pipeline — AMD Developer Hackathon 2026**

Code Guardian scans a GitHub repository for bugs, patches them, writes tests, runs them in an isolated Docker sandbox, and opens a ready-to-merge pull request. Powered by Llama 3.3 70B on AMD Instinct MI300X.

[![Live App](https://img.shields.io/badge/Live%20App-code--guardian.streamlit.app-green)](https://code-guardian.streamlit.app)
[![GitHub](https://img.shields.io/badge/Repo-AbhikRao%2Fcode--guardian-blue)](https://github.com/AbhikRao/code-guardian)
[![Demo Repo](https://img.shields.io/badge/Demo-code--guardian--demo-orange)](https://github.com/AbhikRao/code-guardian-demo)

---

## Pipeline

```
SCANNER → FIXER → TEST WRITER → EXECUTOR → REPORTER
```

| Agent | Responsibility |
|-------|---------------|
| Scanner | pyflakes + Llama 3.3 70B scan — top 15 riskiest files |
| Fixer | Patches top 10 bugs by severity — stdlib only |
| Test Writer | Assertion-driven pytest tests, one per bug |
| Executor | Docker isolated sandbox (`--network none`, `--memory 512m`, `--read-only`) |
| Reporter | GitHub Pull Request with diffs + test results |

---

## Judge Deployment — Running on AMD MI300X

> The live web app at `code-guardian.streamlit.app` uses the Groq API (same Llama 3.3 70B model,
> OpenAI-compatible endpoint). To evaluate the full pipeline on a real AMD Instinct MI300X GPU,
> follow these steps.

### Step 1 — Provision a GPU Droplet

1. Go to [amd.digitalocean.com](https://amd.digitalocean.com)
2. Create → GPU Droplets → **MI300X x1** (1 GPU, 192 GB VRAM, ~$1.99/hr)
3. Select the **vLLM Quick Start** image
4. Add your SSH key → Create Droplet → note the IP address

### Step 2 — Start the Model Server

```bash
ssh root@<DROPLET_IP>

# Start vLLM inside the pre-installed ROCm container
docker exec -d rocm bash -c '
  vllm serve meta-llama/Llama-3.1-8B-Instruct \
    --host 0.0.0.0 --port 8000 \
    --api-key cg-judge-key \
    --max-model-len 4096 > /tmp/vllm.log 2>&1
'

# Wait ~3 minutes, then verify:
curl http://localhost:8000/v1/models
```

### Step 3 — Configure and Run Code Guardian

```bash
git clone https://github.com/AbhikRao/code-guardian
cd code-guardian
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
```
AMD_API_KEY=cg-judge-key
AMD_BASE_URL=http://<DROPLET_IP>:8000/v1
AMD_MODEL_NAME=meta-llama/Llama-3.1-8B-Instruct
GITHUB_TOKEN=<your_github_token>
GITHUB_USERNAME=<your_username>
```

```bash
# CLI (requires Docker for test execution)
python -m core.orchestrator https://github.com/AbhikRao/code-guardian-demo

# Dashboard
streamlit run ui/dashboard.py
```

### Step 4 — Destroy the Droplet

> ⚠️ GPU droplets are billed from creation — destroy immediately after evaluation.

Go to `amd.digitalocean.com` → your droplet → **Destroy**.

---

## Local Setup

```bash
git clone https://github.com/AbhikRao/code-guardian
cd code-guardian
python3.13 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env then:
streamlit run ui/dashboard.py
```

> **Docker is required.** The Executor agent halts with a security error if Docker is
> unavailable — executing AI-generated code outside an isolated container is not permitted.

---

## LLM Fallback Chain

| Tier | Provider | When Used |
|------|---------|-----------|
| 1 | AMD Developer Cloud (MI300X) | Production / judge evaluation |
| 2 | Groq | Development and testing |
| 3 | Ollama local | Offline fallback |

Switching tiers requires changing 3 environment variables — zero code changes.

---

## Environment Variables

```
AMD_API_KEY=       # AMD cloud key or Groq key
AMD_BASE_URL=      # http://<IP>:8000/v1  or  https://api.groq.com/openai/v1
AMD_MODEL_NAME=    # meta-llama/Llama-3.1-8B-Instruct  or  llama-3.3-70b-versatile
GITHUB_TOKEN=      # Personal access token (repo scope)
GITHUB_USERNAME=   # Your GitHub username
```

---

## Security

- **Docker-only execution** — `--network none`, `--memory 512m`, `--read-only`, `--no-new-privileges`. No subprocess fallback.
- **stdlib-only patches** — Fixer is constrained to Python stdlib. No external imports.
- **Read-only sandbox mount** — container cannot write back to the host filesystem.

---

Built by [@AbhikRao](https://github.com/AbhikRao) for the AMD Developer Hackathon 2026 · [lablab.ai](https://lablab.ai/ai-hackathons/amd-developer)
