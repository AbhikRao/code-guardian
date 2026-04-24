"""
fixer.py — Agent 2
———————————————————
Generates patched versions of buggy files.
One LLM call per file — all bugs for that file sent together.

Token budget protection:
  MAX_BUGS_PER_RUN = 10. On large repos the Scanner may find 100+ issues.
  Sending all of them to the Fixer burns the entire daily token budget on
  one call. We take the 10 highest-severity bugs only — enough for a
  compelling demo without exhausting Groq's free tier.

All fixes use Python stdlib only (no bcrypt, no external packages).
"""

import os
import json
from core.llm_client import chat
from core.orchestrator import PipelineState

MAX_BUGS_PER_RUN = 10   # hard cap — prevents rate-limit exhaustion

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

SYSTEM_PROMPT = """You are an expert Python engineer performing a code review fix.
You will be given:
  1. The original source of a Python file
  2. A list of bugs found in that file

Your job: Return the COMPLETE fixed Python file.
Rules:
  - Fix ALL listed bugs. Do not introduce new ones.
  - Keep all existing logic that is NOT buggy.
  - Add a short inline comment on each fixed line: # FIXED: <reason>
  - Return ONLY the raw Python source code. No markdown fences, no explanation.

CRITICAL — Use ONLY Python standard library. No third-party packages.
  - Password hashing: use hashlib.pbkdf2_hmac with os.urandom(16) salt. NEVER bcrypt.
    Example:
      import hashlib, os
      salt = os.urandom(16)
      hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
      return salt.hex() + ':' + hashed.hex()
  - SQL injection: use parameterised queries with ? placeholders. NEVER f-strings in SQL.
  - Path traversal: use os.path.abspath() + startswith() check.
  - JSON errors: use try/except json.JSONDecodeError.
  - File handles: always use `with open(...) as f:` context managers.
  - Division by zero: add explicit `if b == 0: raise ZeroDivisionError(...)` guard.
  - Bare except: replace with `except Exception as e:`.

Do NOT import bcrypt, cryptography, or any package not in Python's standard library.
Any non-stdlib import will cause the tests to fail with an ImportError."""


class FixerAgent:

    def run(self, state: PipelineState) -> PipelineState:
        try:
            # Sort all bugs by severity, take the worst MAX_BUGS_PER_RUN only
            all_bugs = [b for b in state.bug_report
                        if b.get("file") and b["file"] != "test_output"]
            all_bugs.sort(key=lambda b: SEVERITY_ORDER.get(b.get("severity", "low"), 3))
            selected_bugs = all_bugs[:MAX_BUGS_PER_RUN]

            # Group selected bugs by file
            bugs_by_file: dict[str, list] = {}
            for bug in selected_bugs:
                bugs_by_file.setdefault(bug["file"], []).append(bug)

            for rel_path, bugs in bugs_by_file.items():
                abs_path = os.path.join(state.local_path, rel_path)
                if not os.path.exists(abs_path):
                    continue
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    original = f.read()

                # Trim very large files
                lines = original.splitlines()
                if len(lines) > 300:
                    original = "\n".join(lines[:300])

                user_prompt = (
                    f"File: {rel_path}\n\n"
                    f"Bugs to fix:\n{json.dumps(bugs, indent=2)}\n\n"
                    f"Original source:\n```python\n{original}\n```"
                )
                fixed = chat(SYSTEM_PROMPT, user_prompt, temperature=0.1)
                state.patched_files[rel_path] = self._strip_fences(fixed)

        except Exception as e:
            state.error = f"FixerAgent error: {e}"
        return state

    def _strip_fences(self, text: str) -> str:
        lines = text.strip().splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
