"""
scanner.py — Agent 1
─────────────────────
Two-phase scan:
  Phase 1: pyflakes static analysis (fast, free, no tokens)
  Phase 2: LLM deep scan on files flagged by pyflakes or risk keywords

Cloning strategy:
  - Local path: read files directly from disk
  - Remote URL: use GitHub API (PyGithub) — works in cloud environments
    where git credentials are unavailable
"""

import os
import json
import tempfile
import subprocess
from core.llm_client import chat_json
from core.orchestrator import PipelineState

RISK_KEYWORDS = [
    "execute(", "eval(", "exec(", "subprocess", "pickle",
    "md5", "sha1", "password", "secret", "token", "sql",
    "open(", "os.path", "request", "urllib"
]

SYSTEM_PROMPT = """You are a senior Python security and code-quality engineer.
Analyze the Python file below for bugs, security issues, and anti-patterns.

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
        """Use local path directly, or fetch via GitHub API for remote URLs."""
        if state.local_path and os.path.isdir(state.local_path):
            return state

        # Remote GitHub URL — fetch via API (no git credentials needed)
        if "github.com" in state.repo_url:
            state = self._fetch_via_api(state)
        else:
            # Last resort: try git clone
            import git
            tmp = tempfile.mkdtemp(prefix="code_guardian_")
            git.Repo.clone_from(state.repo_url, tmp, depth=1)
            state.local_path = tmp

        return state

    def _fetch_via_api(self, state: PipelineState) -> PipelineState:
        """Download repo files via GitHub API into a temp directory."""
        from github import Github, Auth
        import base64

        # Parse owner/repo from URL
        url = state.repo_url.rstrip("/").rstrip(".git")
        parts = url.split("/")
        owner, repo_name = parts[-2], parts[-1]

        tmp = tempfile.mkdtemp(prefix="code_guardian_")

        # Auth if token available, otherwise anonymous (60 req/hr limit)
        token = os.getenv("GITHUB_TOKEN") or self._get_streamlit_secret("GITHUB_TOKEN")
        g = Github(auth=Auth.Token(token)) if token else Github()

        repo = g.get_repo(f"{owner}/{repo_name}")
        self._fetch_dir(repo, "", tmp)
        state.local_path = tmp
        return state

    def _fetch_dir(self, repo, path, local_root):
        """Recursively fetch Python files from a GitHub repo."""
        skip = {".git", "__pycache__", "venv", ".venv", "node_modules"}
        contents = repo.get_contents(path) if path else repo.get_contents("")
        for item in contents:
            if item.name in skip:
                continue
            local_path = os.path.join(local_root, item.path)
            if item.type == "dir":
                os.makedirs(local_path, exist_ok=True)
                self._fetch_dir(repo, item.path, local_root)
            elif item.name.endswith(".py"):
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(item.decoded_content.decode("utf-8", errors="ignore"))

    def _get_streamlit_secret(self, key: str) -> str:
        try:
            import streamlit as st
            return st.secrets.get(key, "")
        except Exception:
            return ""

    def _collect_python_files(self, root: str) -> list:
        skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist", "build"}
        result = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if fname.endswith(".py"):
                    result.append(os.path.join(dirpath, fname))
        return result

    def _pyflakes_has_issues(self, filepath: str) -> bool:
        result = subprocess.run(
            ["python3", "-m", "pyflakes", filepath],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip() or result.returncode != 0)

    def _has_risk_keywords(self, source: str) -> bool:
        src_lower = source.lower()
        return any(kw in src_lower for kw in RISK_KEYWORDS)

    def _scan_files(self, state: PipelineState) -> PipelineState:
        all_bugs = []
        py_files = self._collect_python_files(state.local_path)

        if not py_files:
            state.error = "No Python files found in repository."
            return state

        for filepath in py_files:
            rel_path = os.path.relpath(filepath, state.local_path)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    source = f.read()
                if len(source.strip()) < 10:
                    continue
                if not (self._pyflakes_has_issues(filepath) or self._has_risk_keywords(source)):
                    continue
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
