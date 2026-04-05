"""
scanner.py — Agent 1
─────────────────────
Two-phase scan:
  Phase 1: pyflakes static analysis (fast, free, no tokens)
  Phase 2: LLM deep scan only on files that pyflakes flagged
            OR files containing known risk keywords

This avoids sending clean files to the LLM entirely,
cutting token usage by ~70% on typical repos.
"""

import os
import json
import tempfile
import subprocess
import git
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

    # ── Path resolution ───────────────────────────────────────────────────────

    def _resolve_path(self, state: PipelineState) -> PipelineState:
        if state.local_path and os.path.isdir(state.local_path):
            return state
        tmp = tempfile.mkdtemp(prefix="code_guardian_")
        git.Repo.clone_from(state.repo_url, tmp, depth=1)
        state.local_path = tmp
        return state

    # ── File collection ───────────────────────────────────────────────────────

    def _collect_python_files(self, root: str) -> list[str]:
        skip_dirs = {".git", "__pycache__", ".venv", "venv",
                     "node_modules", ".tox", "dist", "build", "migrations"}
        result = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if fname.endswith(".py"):
                    result.append(os.path.join(dirpath, fname))
        return result

    # ── Phase 1: pyflakes static filter ──────────────────────────────────────

    def _pyflakes_has_issues(self, filepath: str) -> bool:
        """Return True if pyflakes finds anything in this file."""
        result = subprocess.run(
            ["python3", "-m", "pyflakes", filepath],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip() or result.returncode != 0)

    def _has_risk_keywords(self, source: str) -> bool:
        src_lower = source.lower()
        return any(kw in src_lower for kw in RISK_KEYWORDS)

    def _should_deep_scan(self, filepath: str, source: str) -> bool:
        """Only send to LLM if pyflakes flagged it OR risk keywords present."""
        return self._pyflakes_has_issues(filepath) or self._has_risk_keywords(source)

    # ── Phase 2: LLM deep scan ────────────────────────────────────────────────

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

                # Skip clean files — save tokens for files that need it
                if not self._should_deep_scan(filepath, source):
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
