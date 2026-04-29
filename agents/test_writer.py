"""
test_writer.py — Agent 3
─────────────────────────
Generates AST-inspection tests that verify the fix was applied
by reading the patched source as a STRING and walking its AST.

Why AST inspection instead of importing:
  Importing AI-generated code fails when the Fixer leaves a broken
  import (e.g. 'import bcrypt' with no bcrypt installed). That causes
  an 'environment' failure which is unfixable by retrying.
  AST inspection never imports — it just reads the file and checks
  the syntax tree. This is 100% reliable.
"""

import os
import json
from core.llm_client import chat
from core.orchestrator import PipelineState

SYSTEM_PROMPT = '''You are a senior Python QA engineer writing pytest tests.

You will receive a fixed Python file and the bugs that were fixed.
Write ONE pytest function per bug.

CRITICAL RULE: Do NOT import the patched file. Instead read it as a string:

  SOURCE = open(os.path.join(os.path.dirname(__file__), \'..\', \'FILENAME\')).read()

Then use string searches and ast.parse() to verify the fix.
This avoids ALL import errors permanently.

ALWAYS include this helper at the top of your test file:

```python
"""Tests for fixes in FILENAME."""
import pytest
import ast
import os

SOURCE = open(os.path.join(os.path.dirname(__file__), \'..\', \'FILENAME\')).read()
TREE = ast.parse(SOURCE)


def _fn(name):
    """Return source lines of a named function."""
    lines = SOURCE.splitlines()
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return \'\\n\'.join(lines[node.lineno - 1: node.end_lineno])
    return \'\'
```

TEST PATTERNS BY BUG TYPE:

SQL injection:
  def test_fix_sql_injection():
      fn = _fn(\'create_user\')
      assert fn, \'create_user not found\'
      assert \'f\"\' not in fn and \"f\'\" not in fn, \'f-string SQL still present\'

MD5 / insecure hashing:
  def test_fix_md5_hashing():
      fn = _fn(\'hash_password\')
      assert fn, \'hash_password not found\'
      assert \'md5\' not in fn.lower(), \'MD5 still used\'

Hardcoded secret:
  def test_fix_hardcoded_secret():
      fn = _fn(\'get_env_secret\')
      assert fn, \'get_env_secret not found\'
      assert \'supersecret123hardcoded\' not in fn, \'Hardcoded secret still present\'

Path traversal:
  def test_fix_path_traversal():
      fn = _fn(\'read_user_file\')
      assert fn, \'read_user_file not found\'
      assert \'abspath\' in fn or \'startswith\' in fn or \'ValueError\' in fn, \\
          \'Path traversal not fixed\'

Division by zero:
  def test_fix_division_by_zero():
      fn = _fn(\'divide\')
      assert fn, \'divide not found\'
      assert \'0\' in fn and (\'raise\' in fn or \'if\' in fn), \'Zero guard not added\'

Connection leak:
  def test_fix_connection_leak():
      fn = _fn(\'get_all_users\')
      assert fn, \'get_all_users not found\'
      assert \'close\' in fn or \'with\' in fn, \'Connection leak not fixed\'

Bare except:
  def test_fix_bare_except():
      fn = _fn(\'load_config\')
      assert fn, \'load_config not found\'
      assert \'except:\' not in fn, \'Bare except still present\'

List mutation during iteration:
  def test_fix_list_mutation():
      fn = _fn(\'process_items\')
      assert fn, \'process_items not found\'
      # Fix should use list comprehension or copy — not remove() on same list
      assert \'remove\' not in fn or \'[\' in fn, \'List mutation not fixed\'

File handle leak:
  def test_fix_file_handle_leak():
      fn = _fn(\'read_all_lines\')
      assert fn, \'read_all_lines not found\'
      assert \'with open\' in fn or \'with\' in fn, \'File handle leak not fixed\'

JSON error handling:
  def test_fix_json_error():
      fn = _fn(\'parse_json_data\')
      assert fn, \'parse_json_data not found\'
      assert \'except\' in fn or \'try\' in fn, \'JSON error not handled\'

Empty list / zero division:
  def test_fix_empty_list():
      fn = _fn(\'calculate_average\')
      assert fn, \'calculate_average not found\'
      assert \'if\' in fn or \'raise\' in fn or \'len\' in fn, \'Empty list guard missing\'

RULES:
1. Start with the SOURCE/TREE block above (replace FILENAME with the actual filename)
2. Include the _fn() helper
3. Write one def test_fix_<slug>() per bug
4. Use only string checks and ast on SOURCE — never import the patched module
5. No pytest.raises() that requires importing the patched code
6. No markdown fences in output — raw Python only
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
                    f"Write one AST-inspection test per bug using the SOURCE pattern. "
                    f"Replace FILENAME with '{filename}'. "
                    f"Never import {filename.replace('.py', '')} directly."
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
