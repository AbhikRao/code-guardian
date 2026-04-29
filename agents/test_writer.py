"""
test_writer.py — Agent 3
─────────────────────────
Generates string-search tests on the patched source file.

ARCHITECTURE DECISION:
  Tests NEVER import or ast.parse() the patched file.
  They just open() it and do string searches.

  Why: Any import error, syntax error, or missing dependency in
  the patched file causes an 'environment' failure that cannot be
  retried. String searches on raw text can NEVER fail this way.

  The tradeoff: tests are less precise. The benefit: they always run.
"""

import os
import json
from core.llm_client import chat
from core.orchestrator import PipelineState

SYSTEM_PROMPT = '''You are a senior Python QA engineer writing pytest tests.

You will receive the patched source of a Python file and the bugs that were fixed.
Write ONE pytest function per bug.

MANDATORY APPROACH — string search only, NO imports:

  SOURCE = open(os.path.join(os.path.dirname(__file__), '..', 'FILENAME')).read()

Then search SOURCE as a plain string. Do NOT call ast.parse(). Do NOT import anything
from the patched file. This approach can never fail with an environment error.

TEMPLATE (always start your file like this):

"""Tests for fixes in FILENAME."""
import os

SOURCE = open(os.path.join(os.path.dirname(__file__), '..', 'FILENAME')).read()


def _fn(func_name):
    """Extract approximate source of a function by finding its def line."""
    lines = SOURCE.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(f'def {func_name}('):
            start = i
            break
    if start is None:
        return ''
    result = []
    for line in lines[start:]:
        result.append(line)
        if len(result) > 1 and line.strip() and not line.startswith(' ') and not line.startswith('\\t'):
            break
    return '\\n'.join(result)


EXACT TEST PATTERNS — use these verbatim, substituting function names:

SQL injection:
  def test_fix_sql_injection_create_user():
      fn = _fn('create_user')
      assert 'create_user' in SOURCE, 'create_user not found'
      assert "f'" not in fn and 'f"' not in fn, 'f-string SQL still present'

MD5 hashing:
  def test_fix_md5_hashing():
      assert 'md5' not in SOURCE.lower() or 'pbkdf2' in SOURCE.lower() or 'sha256' in SOURCE.lower(), \\
          'MD5 still used without secure replacement'

Hardcoded secret:
  def test_fix_hardcoded_secret():
      assert 'supersecret123hardcoded' not in SOURCE, 'Hardcoded secret still present'

Path traversal:
  def test_fix_path_traversal():
      fn = _fn('read_user_file')
      assert 'read_user_file' in SOURCE, 'read_user_file not found'
      assert 'abspath' in fn or 'startswith' in fn or 'ValueError' in fn or 'sanitize' in fn, \\
          'Path traversal not fixed'

Division by zero:
  def test_fix_division_by_zero():
      fn = _fn('divide')
      assert 'divide' in SOURCE, 'divide function not found'
      assert 'if' in fn or 'raise' in fn or '== 0' in fn, 'Zero guard not added'

Connection leak:
  def test_fix_connection_leak_get_all_users():
      fn = _fn('get_all_users')
      assert 'get_all_users' in SOURCE, 'get_all_users not found'
      assert 'close' in fn or 'with ' in fn, 'Connection leak not fixed'

Bare except:
  def test_fix_bare_except():
      fn = _fn('load_config')
      assert 'load_config' in SOURCE, 'load_config not found'
      assert 'except:' not in fn, 'Bare except still present'

List mutation:
  def test_fix_list_mutation():
      fn = _fn('process_items')
      assert 'process_items' in SOURCE, 'process_items not found'
      # Fixed version uses list comprehension or copy instead of remove() on same list
      assert '.remove(' not in fn or '[' in fn, 'List mutation bug not fixed'

File handle leak:
  def test_fix_file_handle_leak():
      fn = _fn('read_all_lines')
      assert 'read_all_lines' in SOURCE, 'read_all_lines not found'
      assert 'with open' in fn or 'with ' in fn, 'File handle leak not fixed'

JSON error handling:
  def test_fix_json_error_handling():
      fn = _fn('parse_json_data')
      assert 'parse_json_data' in SOURCE, 'parse_json_data not found'
      assert 'try' in fn or 'except' in fn, 'JSON error not handled'

Empty list / zero division:
  def test_fix_empty_list_divide():
      fn = _fn('calculate_average')
      assert 'calculate_average' in SOURCE, 'calculate_average not found'
      assert 'if' in fn or 'raise' in fn, 'Empty list guard missing'

RULES:
1. Replace FILENAME with the actual filename (e.g. app.py)
2. Always include the _fn() helper
3. One def test_fix_<slug>() per bug
4. ONLY use string operations on SOURCE — no import, no ast.parse()
5. No markdown fences — raw Python only
6. No pytest import needed (no pytest.raises used)
'''


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
                filename     = os.path.basename(rel_path)

                user_prompt = (
                    f"Filename: {filename}\n\n"
                    f"Fixed source:\n```python\n{fixed_source}\n```\n\n"
                    f"Bugs fixed:\n{json.dumps(bugs, indent=2)}\n\n"
                    f"Write one string-search test per bug. "
                    f"Replace FILENAME with '{filename}'. "
                    f"Never import {filename.replace('.py', '')}. "
                    f"Never call ast.parse(). Only use string searches on SOURCE."
                )

                test_source = chat(SYSTEM_PROMPT, user_prompt, temperature=0.05)
                test_source = self._strip_fences(test_source)

                base_name = os.path.splitext(filename)[0]
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
