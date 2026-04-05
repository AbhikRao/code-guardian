"""
executor.py — Agent 4
──────────────────────
Runs generated tests in an isolated sandbox with structured
failure triage to route failures to the correct agent.

  - "patch_wrong"  → Fixer retries
  - "test_wrong"   → TestWriter retries
  - "environment"  → surfaces immediately, no retry

Sandbox strategy:
  - Preferred: Docker container (true isolation)
  - Fallback:  subprocess using the project venv Python
"""

import os
import sys
import json
import shutil
import subprocess
import tempfile
from core.llm_client import chat_json
from core.orchestrator import PipelineState

# Use the same Python interpreter that's running this code
# so the sandbox has access to all installed packages (pytest, etc.)
PYTHON_BIN = sys.executable

TRIAGE_PROMPT = """You are a senior QA engineer reading pytest failure output.
Classify the ROOT CAUSE of the failure.

Return ONLY a JSON object with exactly these keys:
  "cause": one of "patch_wrong" | "test_wrong" | "environment"
  "reason": one sentence explaining why

  "patch_wrong"   = the fixed code has a bug (assertion on correct behaviour fails)
  "test_wrong"    = the test itself is broken (wrong import, impossible assertion,
                    tests something unrelated, would fail even on correct code)
  "environment"   = missing import, missing file, setup error unrelated to logic

No markdown, no extra text — raw JSON only."""


class ExecutorAgent:

    def run(self, state: PipelineState) -> PipelineState:
        try:
            sandbox = self._build_sandbox(state)
            state   = self._run_pytest(state, sandbox)
        except Exception as e:
            state.error = f"ExecutorAgent error: {e}"
        return state

    def _build_sandbox(self, state: PipelineState) -> str:
        sandbox = tempfile.mkdtemp(prefix="cg_sandbox_")
        shutil.copytree(state.local_path, sandbox, dirs_exist_ok=True)

        for rel_path, content in state.patched_files.items():
            abs_path = os.path.join(sandbox, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)

        for rel_path, content in state.test_files.items():
            abs_path = os.path.join(sandbox, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)

        tests_dir = os.path.join(sandbox, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        init_path = os.path.join(tests_dir, "__init__.py")
        if not os.path.exists(init_path):
            open(init_path, "w").close()

        return sandbox

    def _docker_available(self) -> bool:
        try:
            result = subprocess.run(["docker", "info"],
                capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def _run_in_docker(self, sandbox: str) -> tuple[str, int]:
        result = subprocess.run([
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", "512m",
            "--cpus", "1",
            "-v", f"{sandbox}:/app",
            "-w", "/app",
            "python:3.13-slim",
            "bash", "-c",
            "pip install pytest pytest-timeout --quiet 2>/dev/null && "
            "python -m pytest tests/ -v --tb=short --no-header --timeout=30"
        ], capture_output=True, text=True, timeout=180)
        return result.stdout + result.stderr, result.returncode

    def _run_in_subprocess(self, sandbox: str) -> tuple[str, int]:
        """Use the venv Python so all packages (pytest-timeout etc.) are available."""
        result = subprocess.run(
            [PYTHON_BIN, "-m", "pytest", "tests/", "-v",
             "--tb=short", "--no-header", "--timeout=30"],
            capture_output=True, text=True,
            cwd=sandbox, timeout=120
        )
        return result.stdout + result.stderr, result.returncode

    def _run_pytest(self, state: PipelineState, sandbox: str) -> PipelineState:
        if self._docker_available():
            output, returncode = self._run_in_docker(sandbox)
        else:
            output, returncode = self._run_in_subprocess(sandbox)

        state.test_output  = output
        state.tests_passed = (returncode == 0)

        if not state.tests_passed:
            state = self._triage_failure(state, output)

        return state

    def _triage_failure(self, state: PipelineState,
                        pytest_output: str) -> PipelineState:
        try:
            raw    = chat_json(TRIAGE_PROMPT,
                               f"Pytest output:\n```\n{pytest_output[:3000]}\n```")
            triage = json.loads(raw)
            cause  = triage.get("cause", "patch_wrong")
            reason = triage.get("reason", "")
        except Exception:
            cause  = "patch_wrong"
            reason = "Triage failed, defaulting to patch retry"

        state.failure_cause  = cause
        state.failure_reason = reason
        return state
