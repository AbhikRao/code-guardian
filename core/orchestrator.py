"""
orchestrator.py - Master pipeline
Chains all 5 agents with intelligent retry routing.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

MAX_RETRIES = 3


@dataclass
class PipelineState:
    repo_url:       str  = ""
    local_path:     str  = ""

    bug_report:     list = field(default_factory=list)
    patched_files:  dict = field(default_factory=dict)
    test_files:     dict = field(default_factory=dict)
    test_output:    str  = ""
    tests_passed:   bool = False

    failure_cause:  str  = ""
    failure_reason: str  = ""
    sandbox_mode:   str  = ""   # "docker" or "subprocess (Docker unavailable)"

    pr_url:         str  = ""
    retry_count:    int  = 0
    error:          str  = ""


class Orchestrator:
    def __init__(self, on_status: Callable[[str, str], None] | None = None):
        self.on_status = on_status or (lambda s, m: print(f"[{s}] {m}"))

        from agents.scanner     import ScannerAgent
        from agents.fixer       import FixerAgent
        from agents.test_writer import TestWriterAgent
        from agents.executor    import ExecutorAgent
        from agents.reporter    import ReporterAgent

        self.scanner     = ScannerAgent()
        self.fixer       = FixerAgent()
        self.test_writer = TestWriterAgent()
        self.executor    = ExecutorAgent()
        self.reporter    = ReporterAgent()

    def run(self, repo_url: str) -> PipelineState:
        state = PipelineState(repo_url=repo_url)
        try:
            state = self._step_scan(state)
            if state.error: return state

            for attempt in range(1, MAX_RETRIES + 1):
                state.retry_count = attempt

                state = self._step_fix(state)
                if state.error: return state

                if attempt == 1 or state.failure_cause == "test_wrong":
                    state = self._step_write_tests(state)
                    if state.error: return state

                state = self._step_execute(state)
                if state.error: return state

                if state.tests_passed:
                    break

                cause = state.failure_cause
                if cause == "environment":
                    state.error = f"Environment error: {state.failure_reason}\n{state.test_output}"
                    return state

                label = "Fixer" if cause == "patch_wrong" else "TestWriter"
                self.on_status("RETRY", f"Attempt {attempt}/{MAX_RETRIES} failed ({cause}). Routing to {label}...")

            if not state.tests_passed:
                state.error = f"Still failing after {MAX_RETRIES} attempts (last: {state.failure_cause}).\n{state.test_output}"
                return state

            state = self._step_report(state)

        except Exception as exc:
            state.error = f"Pipeline error: {exc}"
            self.on_status("ERROR", state.error)
        return state

    def _step_scan(self, s):
        self.on_status("SCANNER", f"Cloning and scanning repository...")
        s = self.scanner.run(s)
        if not s.error:
            self.on_status("SCANNER", f"Found {len(s.bug_report)} issue(s)")
        return s

    def _step_fix(self, s):
        self.on_status("FIXER", f"Generating patches for {len(s.bug_report)} bug(s)...")
        s = self.fixer.run(s)
        if not s.error:
            self.on_status("FIXER", f"Patched {len(s.patched_files)} file(s)")
        return s

    def _step_write_tests(self, s):
        self.on_status("TEST WRITER", "Writing assertion-driven test suite...")
        s = self.test_writer.run(s)
        if not s.error:
            self.on_status("TEST WRITER", f"Generated {len(s.test_files)} test file(s)")
        return s

    def _step_execute(self, s):
        docker_ok = self.executor._docker_available()
        mode = "Docker" if docker_ok else "subprocess"
        self.on_status("EXECUTOR", f"Running test suite in isolated sandbox ({mode})...")
        s = self.executor.run(s)
        if not s.error:
            status = "All tests passed" if s.tests_passed else f"Tests failed - {s.failure_cause}"
            self.on_status("EXECUTOR", status)
        return s

    def _step_report(self, s):
        self.on_status("REPORTER", "Generating patch report...")
        s = self.reporter.run(s)
        if not s.error:
            self.on_status("REPORTER", "Complete")
        return s


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m core.orchestrator <github-repo-url>")
        sys.exit(1)
    orch   = Orchestrator()
    result = orch.run(sys.argv[1])
    if result.error:
        print(f"\nFailed: {result.error}"); sys.exit(1)
    print(f"\nDone. PR: {result.pr_url}")
