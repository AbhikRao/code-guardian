"""
executor.py - Agent 4
----------------------
Runs generated tests in an isolated execution environment.

Execution tiers:
  TIER 1 - Docker: used when Docker daemon is available
  TIER 2 - Subprocess: used on Streamlit Cloud / no Docker
    - Auto-detects missing modules from pytest output and installs them
    - Retries once after installing missing deps
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
  "test_wrong"    = the test itself is broken (wrong import, bad assertion)
  "environment"   = missing import, package not installed, setup error

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

    def _install_base_deps(self):
        subprocess.run(
            [PYTHON_BIN, "-m", "pip", "install", "pytest", "pytest-timeout",
             "--quiet", "--no-cache-dir"],
            capture_output=True, timeout=60
        )

    def _extract_missing_modules(self, output: str) -> list:
        """Parse pytest output for ModuleNotFoundError and return module names."""
        pattern = r"ModuleNotFoundError: No module named '([^']+)'"
        modules = re.findall(pattern, output)
        # Map common aliases
        alias = {"bcrypt": "bcrypt", "cryptography": "cryptography"}
        return list({alias.get(m, m) for m in modules})

    def _try_install_missing(self, output: str) -> bool:
        """Install any missing modules found in pytest output. Returns True if anything was installed."""
        missing = self._extract_missing_modules(output)
        if not missing:
            return False
        for mod in missing:
            subprocess.run(
                [PYTHON_BIN, "-m", "pip", "install", mod, "--quiet", "--no-cache-dir"],
                capture_output=True, timeout=60
            )
        return True

    def _run_pytest(self, sandbox: str) -> tuple[str, int]:
        result = subprocess.run(
            [PYTHON_BIN, "-m", "pytest", "tests/", "-v",
             "--tb=short", "--no-header", "--timeout=30"],
            capture_output=True, text=True,
            cwd=sandbox, timeout=120,
            env={**os.environ, "PYTHONPATH": sandbox}
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
                "python -m pytest tests/ -v --tb=short --no-header --timeout=30"
            ],
            capture_output=True, text=True, timeout=180
        )
        return self._process_result(state, result.stdout + result.stderr, result.returncode)

    def _run_in_subprocess(self, state: PipelineState, sandbox: str) -> PipelineState:
        """Subprocess mode with auto-install of missing modules."""
        self._install_base_deps()

        # First run
        output, returncode = self._run_pytest(sandbox)

        # If missing modules detected — install them and retry once
        if returncode != 0 and "ModuleNotFoundError" in output:
            installed = self._try_install_missing(output)
            if installed:
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
            raw    = chat_json(TRIAGE_PROMPT, f"Pytest output:\n```\n{pytest_output[:3000]}\n```")
            triage = json.loads(raw)
            cause  = triage.get("cause", "patch_wrong")
            reason = triage.get("reason", "")
        except Exception:
            cause, reason = "patch_wrong", "Triage failed -- defaulting to patch retry"
        state.failure_cause  = cause
        state.failure_reason = reason
        return state
