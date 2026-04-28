"""
scanner.py — Agent 1
─────────────────────
Two-phase scan:
  Phase 1: pyflakes static analysis (fast, free, no tokens)
  Phase 2: LLM deep scan on top-risk files only

Token budget:
  MAX_FILES_PER_RUN = 6  (each file ~6k tokens, 6 files = ~36k tokens)
  MAX_LINES_PER_FILE = 150  (trims large files before sending to LLM)
  This leaves ~60k tokens for the Fixer + TestWriter on Groq's 100k daily limit.

Cloning strategy:
  - Local path: read files directly from disk
  - Remote GitHub URL: use PyGithub API (no git credentials needed)
"""

import os
import json
import tempfile
import subprocess
from core.llm_client import chat_json
from core.orchestrator import PipelineState

MAX_FILES_PER_RUN  = 6    # hard cap — keeps scanner under ~36k tokens
MAX_LINES_PER_FILE = 150  # trim large files before LLM call

RISK_KEYWORDS = [
    "execute(", "eval(", "exec(", "subprocess", "pickle",
    "md5", "sha1", "password", "secret", "token", "sql",
    "open(", "os.path", "request", "urllib", "cursor",
    "getenv", "hardcode", "inject"
]

SYSTEM_PROMPT = """You are a senior Python security and code-quality engineer.
Analyze the Python code below for bugs, security issues, and anti-patterns.

Return ONLY a JSON array. Each element must have exactly these keys:
  "file"     - filename (string)
  "line"     - approximate line number (integer)
  "issue"    - one sentence describing the problem
  "severity" - one of: "critical", "high", "medium", "low"
  "snippet"  - the exact problematic line(s) copied verbatim

Return [] if no issues found. No markdown, no prose — raw JSON only."""


class ScannerAgent:

    def run(self, state: PipelineState) -> PipelineState:
        try:
            state = self._resolve_path(state)
            state = self._scan_files(state)
        except Exception as e:
            state.error = f"ScannerAgent error: {e}"
        return state

    def _resolve_path(self, state: PipelineState) -> PipelineState:
        if state.local_path and os.path.isdir(state.local_path):
            return state
        if "github.com" in state.repo_url:
            state = self._fetch_via_api(state)
        else:
            import git
            tmp = tempfile.mkdtemp(prefix="code_guardian_")
            git.Repo.clone_from(state.repo_url, tmp, depth=1)
            state.local_path = tmp
        return state

    def _fetch_via_api(self, state: PipelineState) -> PipelineState:
        from github import Github, Auth
        url   = state.repo_url.rstrip("/").rstrip(".git")
        parts = url.split("/")
        owner, repo_name = parts[-2], parts[-1]
        tmp   = tempfile.mkdtemp(prefix="code_guardian_")
        token = self._get_secret("GITHUB_TOKEN")
        g     = Github(auth=Auth.Token(token)) if token else Github()
        repo  = g.get_repo(f"{owner}/{repo_name}")
        self._fetch_dir(repo, "", tmp, depth=0)
        state.local_path = tmp
        return state

    def _fetch_dir(self, repo, path, local_root, depth=0):
        if depth > 4:
            return
        skip = {".git", "__pycache__", "venv", ".venv", "node_modules",
                "dist", "build", ".tox", "migrations"}
        try:
            contents = repo.get_contents(path) if path else repo.get_contents("")
        except Exception:
            return
        for item in contents:
            if item.name in skip:
                continue
            local_path = os.path.join(local_root, item.path)
            if item.type == "dir":
                os.makedirs(local_path, exist_ok=True)
                self._fetch_dir(repo, item.path, local_root, depth + 1)
            elif item.name.endswith(".py") or item.name.endswith(".ipynb"):
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                try:
                    with open(local_path, "w", encoding="utf-8") as f:
                        f.write(item.decoded_content.decode("utf-8", errors="ignore"))
                except Exception:
                    pass

    def _get_secret(self, key: str) -> str:
        val = os.getenv(key, "")
        if not val:
            try:
                import streamlit as st
                val = st.secrets.get(key, "")
            except Exception:
                pass
        return val

    def _collect_files(self, root: str) -> list:
        skip_dirs = {".git", "__pycache__", ".venv", "venv",
                     "node_modules", "dist", "build", "migrations"}
        result = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if fname.endswith(".py") or fname.endswith(".ipynb"):
                    result.append(os.path.join(dirpath, fname))
        return result

    def _extract_source(self, filepath: str) -> str:
        if filepath.endswith(".ipynb"):
            try:
                nb = json.loads(open(filepath, encoding="utf-8", errors="ignore").read())
                lines = []
                for cell in nb.get("cells", []):
                    if cell.get("cell_type") == "code":
                        src = cell.get("source", [])
                        lines.extend(src if isinstance(src, list) else [src])
                return "".join(lines)
            except Exception:
                return ""
        return open(filepath, "r", encoding="utf-8", errors="ignore").read()

    def _risk_score(self, source: str) -> int:
        src_lower = source.lower()
        return sum(src_lower.count(kw) for kw in RISK_KEYWORDS)

    def _pyflakes_has_issues(self, filepath: str) -> bool:
        if filepath.endswith(".ipynb"):
            return True
        result = subprocess.run(
            ["python3", "-m", "pyflakes", filepath],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip() or result.returncode != 0)

    def _scan_files(self, state: PipelineState) -> PipelineState:
        all_files = self._collect_files(state.local_path)
        if not all_files:
            state.error = "No Python files found in repository."
            return state

        # Score every file, keep top MAX_FILES_PER_RUN by risk
        scored = []
        for filepath in all_files:
            source = self._extract_source(filepath)
            if len(source.strip()) < 10:
                continue
            score = self._risk_score(source)
            if score > 0 or self._pyflakes_has_issues(filepath):
                scored.append((score, filepath, source))

        scored.sort(key=lambda x: x[0], reverse=True)
        to_scan = scored[:MAX_FILES_PER_RUN]

        all_bugs = []
        for _, filepath, source in to_scan:
            rel_path = os.path.relpath(filepath, state.local_path)
            try:
                lines = source.splitlines()
                if len(lines) > MAX_LINES_PER_FILE:
                    source = "\n".join(lines[:MAX_LINES_PER_FILE])

                user_prompt = f"File: {rel_path}\n\n```python\n{source}\n```"
                raw  = chat_json(SYSTEM_PROMPT, user_prompt)
                bugs = json.loads(raw)
                for bug in bugs:
                    bug["file"] = rel_path
                all_bugs.extend(bugs)
            except json.JSONDecodeError:
                continue
            except Exception:
                continue

        state.bug_report = all_bugs
        return state
