"""
executor.py - Agent 4
----------------------
Runs generated tests in an isolated execution environment.

Execution tiers:
  TIER 1 - Docker: used when Docker daemon is available
  TIER 2 - Subprocess: used on Streamlit Cloud / no Docker

Key fix: do NOT create __init__.py in the tests directory.
When tests/__init__.py exists, pytest treats tests as a package
and import resolution breaks for app/utils in the sandbox root.
Without __init__.py, pytest uses rootdir + PYTHONPATH correctly.
"""

import os
import re
import sys
import json
import shutil
import subprocess
import tempfile
from core.llm_client import chat_json
from core.orchestrator import PipelineState

PYTHON_BIN = sys.executable

TRIAGE_PROMPT = """You are a senior QA engineer reading pytest failure output.
Classify the ROOT CAUSE of the failure.

Return ONLY a JSON object with exactly these keys:
  "cause":  one of "patch_wrong" | "test_wrong" | "environment"
  "reason": one sentence explaining why

  "patch_wrong"   = the fixed code has a bug (assertion on correct behaviour fails)
  "test_wrong"    = the test itself is broken (wrong import path, bad assertion,
                    imports a name that doesn't exist in the module)
  "environment"   = pip package genuinely missing, unresolvable OS error

IMPORTANT: If the failure is ModuleNotFoundError for 'app' or 'utils',
that is 'test_wrong' (wrong import path), NOT 'environment'.
Only classify as 'environment' if a third-party pip package is missing.

No markdown, no extra text -- raw JSON only."""


class ExecutorAgent:

    def _docker_available(self) -> bool:
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=8)
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def run(self, state: PipelineState) -> PipelineState:
        try:
            sandbox = self._build_sandbox(state)
            if self._docker_available():
                state.sandbox_mode = "docker"
                state = self._run_in_docker(state, sandbox)
            else:
                state.sandbox_mode = "subprocess"
                state = self._run_in_subprocess(state, sandbox)
        except Exception as e:
            state.error = f"ExecutorAgent error: {e}"
        return state

    def _build_sandbox(self, state: PipelineState) -> str:
        sandbox = tempfile.mkdtemp(prefix="cg_sandbox_")
        shutil.copytree(state.local_path, sandbox, dirs_exist_ok=True)

        # Write patched source files
        for rel_path, content in state.patched_files.items():
            abs_path = os.path.join(sandbox, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)

        # Write test files — NO __init__.py in tests/
        # __init__.py breaks pytest's rootdir import resolution
        tests_dir = os.path.join(sandbox, "tests")
        os.makedirs(tests_dir, exist_ok=True)

        # Remove any existing __init__.py that could break imports
        init_path = os.path.join(tests_dir, "__init__.py")
        if os.path.exists(init_path):
            os.remove(init_path)

        for rel_path, content in state.test_files.items():
            abs_path = os.path.join(sandbox, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)

        return sandbox

    def _install_base_deps(self):
        subprocess.run(
            [PYTHON_BIN, "-m", "pip", "install", "pytest", "pytest-timeout",
             "--quiet", "--no-cache-dir"],
            capture_output=True, timeout=60
        )

    def _run_pytest(self, sandbox: str) -> tuple[str, int]:
        """Run pytest with importlib mode for robust import resolution."""
        result = subprocess.run(
            [
                PYTHON_BIN, "-m", "pytest", "tests/",
                "-v", "--tb=short", "--no-header", "--timeout=30",
                "--import-mode=importlib",  # avoids __init__.py import issues
            ],
            capture_output=True, text=True,
            cwd=sandbox, timeout=120,
            env={
                **os.environ,
                "PYTHONPATH": sandbox,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return result.stdout + result.stderr, result.returncode

    def _run_in_docker(self, state: PipelineState, sandbox: str) -> PipelineState:
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", "512m",
                "--cpus", "1",
                "--read-only",
                "--no-new-privileges",
                "--tmpfs", "/tmp",
                "-v", f"{sandbox}:/app:ro",
                "-w", "/app",
                "python:3.13-slim",
                "bash", "-c",
                "pip install pytest pytest-timeout --quiet --no-cache-dir 2>/dev/null && "
                "python -m pytest tests/ -v --tb=short --no-header --timeout=30 "
                "--import-mode=importlib"
            ],
            capture_output=True, text=True, timeout=180
        )
        return self._process_result(state, result.stdout + result.stderr, result.returncode)

    def _run_in_subprocess(self, state: PipelineState, sandbox: str) -> PipelineState:
        self._install_base_deps()
        output, returncode = self._run_pytest(sandbox)
        return self._process_result(state, output, returncode)

    def _process_result(self, state: PipelineState, output: str, returncode: int) -> PipelineState:
        state.test_output  = output
        state.tests_passed = (returncode == 0)
        if not state.tests_passed:
            state = self._triage_failure(state, output)
        return state

    def _triage_failure(self, state: PipelineState, pytest_output: str) -> PipelineState:
        try:
            raw    = chat_json(
                TRIAGE_PROMPT,
                f"Pytest output:\n```\n{pytest_output[:3000]}\n```"
            )
            triage = json.loads(raw)
            cause  = triage.get("cause",  "test_wrong")  # default to test_wrong not patch_wrong
            reason = triage.get("reason", "")
        except Exception:
            cause  = "test_wrong"
            reason = "Triage failed -- defaulting to test_wrong retry"
        state.failure_cause  = cause
        state.failure_reason = reason
        return state
