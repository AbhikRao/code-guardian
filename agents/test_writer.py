"""
test_writer.py — Agent 3
─────────────────────────
Generates targeted pytest tests, one per bug, assertion-driven.
"""

import os
import json
from core.llm_client import chat
from core.orchestrator import PipelineState

SYSTEM_PROMPT = """You are a senior Python QA engineer writing targeted pytest tests.

You will receive:
  1. A fixed Python file
  2. The exact list of bugs that were fixed

YOUR ONLY JOB: Write exactly ONE pytest test per bug that:
  - Is named: test_fix_<short_slug_of_issue>
  - Proves the specific bug no longer exists with the SIMPLEST assertion possible
  - Uses unittest.mock to patch sqlite3, open, os.path etc — never hits real filesystem or DB
  - Would FAIL on the buggy original and PASS on the fixed version

STRICT RULES — violating any of these will cause test errors:
  1. NEVER use pytest.any() or pytest.match() — they do not exist as functions.
     Use unittest.mock.ANY for wildcard matching in assert_called_with().
  2. For hash/bytes results: use isinstance(result, str) or result != original_value.
     NEVER call .startswith() on a value that might be bytes.
  3. For path traversal tests: assert pytest.raises(ValueError) or pytest.raises(Exception).
     NEVER try to actually open a non-existent file — it will raise FileNotFoundError not ValueError.
  4. For SQL injection tests: mock sqlite3.connect and assert the cursor.execute was called
     with a parameterised query tuple, e.g. assert_called_once_with("SELECT...", (value,)).
     Use unittest.mock.ANY for argument positions you don't know exactly.
  5. For connection leak tests: use a context manager mock or simply assert the function
     returns the expected result without checking internal close() calls.
  6. ONLY import: pytest, unittest.mock, and the module under test.

Start the file with a docstring: \"\"\"Tests for fixes in <filename>.\"\"\"
Return ONLY raw Python source. No markdown fences, no explanation."""


class TestWriterAgent:

    def run(self, state: PipelineState) -> PipelineState:
        try:
            bugs_by_file: dict[str, list] = {}
            for bug in state.bug_report:
                if bug["file"] == "test_output":
                    continue
                bugs_by_file.setdefault(bug["file"], []).append(bug)

            for rel_path, bugs in bugs_by_file.items():
                if rel_path not in state.patched_files:
                    continue

                fixed_source = state.patched_files[rel_path]
                bugs_text    = json.dumps(bugs, indent=2)
                module_name  = rel_path.replace("/", ".").replace(".py", "")

                user_prompt = (
                    f"Module: {rel_path} (import as: {module_name})\n\n"
                    f"Bugs fixed:\n{bugs_text}\n\n"
                    f"Fixed source:\n```python\n{fixed_source}\n```\n\n"
                    f"Remember the strict rules above — especially:\n"
                    f"- Use unittest.mock.ANY not pytest.any()\n"
                    f"- Never call .startswith() on bytes\n"
                    f"- Path traversal: assert raises Exception, don't open real files\n"
                    f"- SQL tests: mock sqlite3 and check parameterised call"
                )

                test_source = chat(SYSTEM_PROMPT, user_prompt, temperature=0.1)
                test_source = self._strip_fences(test_source)

                base_name = os.path.splitext(os.path.basename(rel_path))[0]
                state.test_files[f"tests/test_{base_name}.py"] = test_source

        except Exception as e:
            state.error = f"TestWriterAgent error: {e}"
        return state

    def _strip_fences(self, text: str) -> str:
        lines = text.strip().splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
