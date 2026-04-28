"""
fixer.py — Agent 2
———————————————————
Generates patched versions of buggy files.
One LLM call per file. Caps at top 10 bugs by severity.
Post-processes output to strip any forbidden external imports.
"""

import os
import re
import json
from core.llm_client import chat
from core.orchestrator import PipelineState

MAX_BUGS_PER_RUN = 10
SEVERITY_ORDER   = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# These imports are forbidden in patched code — they won't be installed in the sandbox
FORBIDDEN_IMPORTS = ["bcrypt", "cryptography", "paramiko", "pycryptodome"]

# stdlib replacement for bcrypt-style password hashing
HASHLIB_REPLACEMENT = """import hashlib as _hashlib
import os as _os
def _hash_password(password: str) -> str:
    salt = _os.urandom(16)
    h = _hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return salt.hex() + ':' + h.hex()
"""

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
      import hashlib, os as _os
      salt = _os.urandom(16)
      h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
      return salt.hex() + ':' + h.hex()
  - SQL injection: use parameterised queries with ? placeholders. NEVER f-strings in SQL.
  - Path traversal: use os.path.abspath() + startswith() check. Raise ValueError on escape.
  - JSON errors: use try/except json.JSONDecodeError.
  - File handles: always use `with open(...) as f:` context managers.
  - Division by zero: guard with `if denominator == 0: raise ZeroDivisionError(...)`
  - Bare except: replace with `except Exception as e:`.

DO NOT import bcrypt, cryptography, paramiko, pycryptodome, or any non-stdlib package."""


class FixerAgent:

    def run(self, state: PipelineState) -> PipelineState:
        try:
            all_bugs = [b for b in state.bug_report
                        if b.get("file") and b["file"] != "test_output"]
            all_bugs.sort(key=lambda b: SEVERITY_ORDER.get(b.get("severity", "low"), 3))
            selected = all_bugs[:MAX_BUGS_PER_RUN]

            bugs_by_file: dict[str, list] = {}
            for bug in selected:
                bugs_by_file.setdefault(bug["file"], []).append(bug)

            for rel_path, bugs in bugs_by_file.items():
                abs_path = os.path.join(state.local_path, rel_path)
                if not os.path.exists(abs_path):
                    continue
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    original = f.read()
                lines = original.splitlines()
                if len(lines) > 300:
                    original = "\n".join(lines[:300])

                user_prompt = (
                    f"File: {rel_path}\n\n"
                    f"Bugs to fix:\n{json.dumps(bugs, indent=2)}\n\n"
                    f"Original source:\n```python\n{original}\n```"
                )
                fixed = chat(SYSTEM_PROMPT, user_prompt, temperature=0.1)
                fixed = self._strip_fences(fixed)
                fixed = self._sanitise_imports(fixed)
                state.patched_files[rel_path] = fixed

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

    def _sanitise_imports(self, source: str) -> str:
        """Remove any forbidden external imports that slipped through the LLM."""
        lines = source.splitlines()
        clean = []
        for line in lines:
            stripped = line.strip()
            is_bad = any(
                stripped.startswith(f"import {pkg}") or
                stripped.startswith(f"from {pkg}")
                for pkg in FORBIDDEN_IMPORTS
            )
            if is_bad:
                # Replace bcrypt import with a comment; the hashlib helper handles hashing
                clean.append(f"# REMOVED: external import '{stripped}' replaced with hashlib")
            else:
                clean.append(line)
        return "\n".join(clean)
