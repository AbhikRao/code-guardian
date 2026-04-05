"""
fixer.py
────────
Agent 2: Takes the bug report from Scanner and generates
a fixed version of each affected file.

Output: state.patched_files → { "src/app.py": "<full fixed source>", ... }
"""

import os
import json
from core.llm_client import chat
from core.orchestrator import PipelineState

SYSTEM_PROMPT = """You are an expert Python engineer performing a code review fix.
You will be given:
  1. The original source of a Python file
  2. A list of bugs found in that file (with line numbers and descriptions)

Your job: Return the COMPLETE fixed Python file.
Rules:
  - Fix ALL listed bugs. Do not introduce new ones.
  - Keep all existing logic that is NOT buggy — do not refactor unnecessarily.
  - Add a short inline comment on each fixed line: # FIXED: <reason>
  - Return ONLY the raw Python source code. No markdown fences, no explanation."""


class FixerAgent:

    def run(self, state: PipelineState) -> PipelineState:
        try:
            # Group bugs by file so we send one request per file
            bugs_by_file: dict[str, list] = {}
            for bug in state.bug_report:
                bugs_by_file.setdefault(bug["file"], []).append(bug)

            for rel_path, bugs in bugs_by_file.items():
                abs_path = os.path.join(state.local_path, rel_path)
                if not os.path.exists(abs_path):
                    continue

                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    original_source = f.read()

                bugs_text = json.dumps(bugs, indent=2)
                user_prompt = (
                    f"File: {rel_path}\n\n"
                    f"Bugs to fix:\n{bugs_text}\n\n"
                    f"Original source:\n```python\n{original_source}\n```"
                )

                fixed_source = chat(SYSTEM_PROMPT, user_prompt, temperature=0.1)

                # Strip accidental markdown fences if the model added them
                fixed_source = self._strip_fences(fixed_source)
                state.patched_files[rel_path] = fixed_source

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
