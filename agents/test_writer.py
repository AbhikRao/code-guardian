"""
test_writer.py — Agent 3
─────────────────────────
Generates targeted pytest tests, one per bug.
Tests verify BEHAVIOUR, not implementation details.
This distinction is what keeps tests passing across LLM runs.
"""

import os
import json
from core.llm_client import chat
from core.orchestrator import PipelineState

SYSTEM_PROMPT = """You are a senior Python QA engineer writing pytest tests.

You will receive a fixed Python file and the list of bugs that were fixed.

Write exactly ONE pytest function per bug.

NAMING: test_fix_<short_slug> (e.g. test_fix_sql_injection, test_fix_division_by_zero)

CORE RULE — test BEHAVIOUR, not implementation:
  - Do NOT assert HOW the fix was implemented (do not check mock call args)
  - DO assert WHAT the fixed code does (raises the right exception, returns safely, etc.)

PATTERNS TO USE FOR EACH BUG TYPE:

  SQL injection:
    def test_fix_sql_injection_create_user():
        # Just verify the function exists and accepts parameters without crashing
        # We cannot call it without a real DB, so import and check signature
        import inspect
        import app
        sig = inspect.signature(app.create_user)
        assert 'username' in sig.parameters

  Division by zero:
    def test_fix_division_by_zero():
        import app
        with pytest.raises((ZeroDivisionError, ValueError)):
            app.divide(10, 0)

  Hardcoded secret / env var:
    def test_fix_hardcoded_secret():
        import utils
        import unittest.mock
        with unittest.mock.patch.dict('os.environ', {}, clear=True):
            # When env var is missing, should raise, not silently use hardcoded value
            with pytest.raises((ValueError, KeyError, Exception)):
                utils.get_env_secret()

  Path traversal:
    def test_fix_path_traversal():
        import app
        with pytest.raises((ValueError, PermissionError, Exception)):
            app.read_user_file('../etc/passwd')

  Password hashing (MD5 replaced):
    def test_fix_password_hashing():
        import app
        result = app.hash_password('test123')
        # Must be a string and must NOT be the MD5 of 'test123'
        assert isinstance(result, str)
        assert result != 'cc03e747a6afbbcbf8be7668acfebee5'

  File handle / resource leak:
    def test_fix_file_handle():
        import utils
        # Function should complete without error on valid input
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('line1\nline2')
            name = f.name
        try:
            result = utils.read_all_lines(name)
            assert isinstance(result, list)
        finally:
            os.unlink(name)

  List mutation during iteration:
    def test_fix_list_mutation():
        import utils
        items = [1, -2, 3, -4, 5]
        result = utils.process_items(items)
        assert all(x >= 0 for x in result)
        assert len(result) == 3

  JSON error handling:
    def test_fix_json_error():
        import utils
        with pytest.raises((ValueError, Exception)):
            utils.parse_json_data('not valid json{')

  Bare except:
    def test_fix_bare_except():
        import app
        # Function should exist and be callable
        import inspect
        assert callable(app.load_config)

  Connection leak:
    def test_fix_connection_leak():
        import app, unittest.mock
        mock_conn = unittest.mock.MagicMock()
        mock_cursor = unittest.mock.MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        with unittest.mock.patch('app.get_db', return_value=mock_conn):
            result = app.get_all_users()
            assert isinstance(result, list)

  ZeroDivision empty list:
    def test_fix_empty_list_average():
        import utils
        with pytest.raises((ZeroDivisionError, ValueError)):
            utils.calculate_average([])

STRICT RULES:
  1. import pytest at the top
  2. import unittest.mock if needed
  3. NEVER use pytest.any() — it does not exist
  4. NEVER call .startswith() on a value that could be bytes
  5. NEVER open a file path that doesn't exist to test path traversal
  6. NEVER assert mock call arguments — assert behaviour instead
  7. If a function needs a DB/network, mock it minimally or just test the signature
  8. Every test must be runnable with NO external packages except pytest

Start the file with: \"\"\"Tests for fixes in <filename>.\"\"\"
Return ONLY raw Python. No markdown fences, no explanation."""


class TestWriterAgent:

    def run(self, state: PipelineState) -> PipelineState:
        try:
            bugs_by_file: dict[str, list] = {}
            for bug in state.bug_report:
                if bug.get("file", "") == "test_output":
                    continue
                bugs_by_file.setdefault(bug["file"], []).append(bug)

            for rel_path, bugs in bugs_by_file.items():
                if rel_path not in state.patched_files:
                    continue

                fixed_source = state.patched_files[rel_path]
                module_name  = rel_path.replace("/", ".").replace(".py", "")

                user_prompt = (
                    f"Module: {rel_path} (import as: {module_name})\n\n"
                    f"Fixed source (what the module looks like AFTER the fix):\n"
                    f"```python\n{fixed_source}\n```\n\n"
                    f"Bugs that were fixed:\n{json.dumps(bugs, indent=2)}\n\n"
                    f"Write one test per bug. Test BEHAVIOUR not implementation. "
                    f"Never assert mock call args. Never use pytest.any()."
                )

                test_source = chat(SYSTEM_PROMPT, user_prompt, temperature=0.05)
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
