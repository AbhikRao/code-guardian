"""
executor.py - Agent 4
----------------------
Runs generated tests inside an isolated Docker container.

SECURITY POLICY:
  This agent executes AI-generated code. Isolation is mandatory.
  Docker is the ONLY supported execution environment.

  If Docker is unavailable the pipeline halts immediately with a
  descriptive error. The subprocess fallback has been permanently
  removed: executing untrusted AI-generated code in the host venv
  is a critical security vulnerability, especially on shared cloud
  infrastructure such as Streamlit Community Cloud.

Docker constraints per run:
  --network none          no internet access from inside container
  --memory 512m           memory cap
  --cpus 1                CPU cap
  --read-only             filesystem is read-only except /tmp
  --no-new-privileges     blocks privilege escalation
  --tmpfs /tmp            writable temp dir, destroyed on exit
  -v sandbox:/app:ro      sandbox mounted read-only
  python:3.13-slim        minimal throwaway image
"""

import os
import json
import shutil
import subprocess
import tempfile
from core.llm_client import chat_json
from core.orchestrator import PipelineState

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
        Execute tests in an isolated Docker container.
        Halts immediately if Docker is not available.
        No subprocess fallback -- see security policy in module docstring.
        """
        if not self._docker_available():
            state.error = (
                "SECURITY HALT: Docker is not available on this system.\n\n"
                "Code Guardian requires Docker to execute AI-generated test "
                "code in an isolated container. Running untrusted code in the "
                "host environment is a critical security vulnerability.\n\n"
                "To resolve:\n"
                "  1. Install Docker: https://docs.docker.com/get-docker/\n"
                "  2. Start the Docker daemon\n"
                "  3. Re-run the pipeline\n\n"
                "Note: Streamlit Community Cloud does not support Docker.\n"
                "Run Code Guardian locally or on the AMD MI300X droplet "
                "(see README.md -- Judge Deployment section)."
            )
            return state

        try:
            sandbox = self._build_sandbox(state)
            state   = self._run_in_docker(state, sandbox)
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
            with open(abs_path, "w", encoding="utf-8") as f:\
                f.write(content)

        tests_dir = os.path.join(sandbox, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        if not os.path.exists(os.path.join(tests_dir, "__init__.py")):
            open(os.path.join(tests_dir, "__init__.py"), "w").close()

        return sandbox

    def _run_in_docker(self, state: PipelineState, sandbox: str) -> PipelineState:
        """Run pytest inside a throwaway container with strict isolation."""
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
        output = result.stdout + result.stderr
        state.test_output  = output
        state.tests_passed = (result.returncode == 0)
        if not state.tests_passed:
            state = self._triage_failure(state, output)
        return state

    def _triage_failure(self, state: PipelineState,
                        pytest_output: str) -> PipelineState:
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
