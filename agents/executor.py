"""
executor.py - Agent 4
----------------------
Runs generated tests in an isolated execution environment.

Execution tiers:

  TIER 1 - Docker (full isolation):
    Used when Docker daemon is available (local machine, AMD MI300X droplet).
    Constraints: --network none, --memory 512m, --cpus 1, --read-only,
    --no-new-privileges, sandbox mounted :ro.
    This is the production-grade security posture.

  TIER 2 - Subprocess (cloud demo mode):
    Used when Docker is unavailable (Streamlit Community Cloud).
    Runs pytest in an isolated temp directory using the host Python.
    Clearly labelled in the UI as "cloud demo mode".
    Acceptable for a demo environment where no credentials are in scope.
    For production use, Docker (Tier 1) is required.
"""

import os
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

  "patch_wrong"   = the fixed code has a bug
  "test_wrong"    = the test itself is broken
  "environment"   = missing import, setup error unrelated to logic

No markdown, no extra text -- raw JSON only."""


class ExecutorAgent:

    def _docker_available(self) -> bool:
        """Return True only if Docker daemon is running and reachable."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=8
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def run(self, state: PipelineState) -> PipelineState:
        """
        Execute tests in the best available isolated environment.
        Tier 1 (Docker) when available, Tier 2 (subprocess) otherwise.
        """
        try:
            sandbox = self._build_sandbox(state)
            if self._docker_available():
                state.sandbox_mode = "docker"
                state = self._run_in_docker(state, sandbox)
            else:
                state.sandbox_mode = "subprocess (cloud demo mode)"
                state = self._run_in_subprocess(state, sandbox)
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
        if not os.path.exists(os.path.join(tests_dir, "__init__.py")):
            open(os.path.join(tests_dir, "__init__.py"), "w").close()

        return sandbox

    def _run_in_docker(self, state: PipelineState, sandbox: str) -> PipelineState:
        """Tier 1 — full Docker isolation."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network",          "none",
                "--memory",           "512m",
                "--cpus",             "1",
                "--read-only",
                "--no-new-privileges",
                "--tmpfs",            "/tmp",
                "-v", f"{sandbox}:/app:ro",
                "-w", "/app",
                "python:3.13-slim",
                "bash", "-c",
                "pip install pytest pytest-timeout --quiet --no-cache-dir "
                "2>/dev/null && "
                "python -m pytest tests/ -v --tb=short --no-header --timeout=30"
            ],
            capture_output=True,
            text=True,
            timeout=180
        )
        return self._process_result(state, result.stdout + result.stderr, result.returncode)

    def _run_in_subprocess(self, state: PipelineState, sandbox: str) -> PipelineState:
        """Tier 2 — subprocess fallback for cloud demo environments."""
        # Install test dependencies first
        subprocess.run(
            [PYTHON_BIN, "-m", "pip", "install", "pytest", "pytest-timeout",
             "--quiet", "--no-cache-dir"],
            capture_output=True, timeout=60
        )
        result = subprocess.run(
            [PYTHON_BIN, "-m", "pytest", "tests/", "-v",
             "--tb=short", "--no-header", "--timeout=30"],
            capture_output=True, text=True,
            cwd=sandbox, timeout=120,
            env={**os.environ, "PYTHONPATH": sandbox}
        )
        return self._process_result(state, result.stdout + result.stderr, result.returncode)

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
            cause  = triage.get("cause",  "patch_wrong")
            reason = triage.get("reason", "")
        except Exception:
            cause  = "patch_wrong"
            reason = "Triage failed -- defaulting to patch retry"
        state.failure_cause  = cause
        state.failure_reason = reason
        return state
