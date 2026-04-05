"""
reporter.py
───────────
Agent 5: Creates a GitHub Pull Request with:
  - A clean diff of all patched files
  - A human-readable AI-generated summary of what was fixed
  - The full pytest test results attached

Output: state.pr_url → "https://github.com/user/repo/pull/42"
"""

import os
from github import Github, GithubException
from core.llm_client import chat
from core.orchestrator import PipelineState

SUMMARY_PROMPT = """You are a senior engineer writing a GitHub Pull Request description.
Given a list of bugs that were found and fixed in a codebase, write a clear,
professional PR description in Markdown that includes:
  - A one-sentence summary of what this PR does
  - A "## Changes" section listing each fix with file, issue, and severity
  - A "## Testing" section confirming all tests passed

Keep it concise and factual. Use proper Markdown."""


class ReporterAgent:

    def run(self, state: PipelineState) -> PipelineState:
        try:
            token = os.getenv("GITHUB_TOKEN", "")
            if not token:
                # No token: save patch files locally instead of opening a PR
                state = self._save_patch_locally(state)
                return state

            state = self._open_pull_request(state, token)
        except Exception as e:
            state.error = f"ReporterAgent error: {e}"
        return state

    def _open_pull_request(self, state: PipelineState,
                           token: str) -> PipelineState:
        g    = Github(token)
        repo = g.get_repo(self._repo_name(state.repo_url))

        branch_name = "code-guardian/auto-fix"

        # Get default branch SHA
        default_branch = repo.default_branch
        ref = repo.get_git_ref(f"heads/{default_branch}")
        base_sha = ref.object.sha

        # Create patch branch (delete if already exists)
        try:
            repo.get_git_ref(f"heads/{branch_name}").delete()
        except GithubException:
            pass
        repo.create_git_ref(f"refs/heads/{branch_name}", base_sha)

        # Push each patched file
        for rel_path, content in state.patched_files.items():
            try:
                existing = repo.get_contents(rel_path, ref=branch_name)
                repo.update_file(rel_path,
                                 f"fix: auto-patch {rel_path}",
                                 content, existing.sha,
                                 branch=branch_name)
            except GithubException:
                repo.create_file(rel_path,
                                 f"fix: auto-patch {rel_path}",
                                 content,
                                 branch=branch_name)

        pr_body = self._generate_pr_body(state)
        pr = repo.create_pull(
            title="🤖 Code Guardian: Auto-fix detected issues",
            body=pr_body,
            head=branch_name,
            base=default_branch
        )
        state.pr_url = pr.html_url
        return state

    def _generate_pr_body(self, state: PipelineState) -> str:
        import json
        bugs_text = json.dumps(state.bug_report, indent=2)
        summary = chat(
            SUMMARY_PROMPT,
            f"Bugs fixed:\n{bugs_text}\n\nTest output:\n{state.test_output[:1000]}",
            temperature=0.3
        )
        return summary + f"\n\n---\n<details><summary>Full test output</summary>\n\n```\n{state.test_output}\n```\n</details>"

    def _save_patch_locally(self, state: PipelineState) -> PipelineState:
        """Fallback: write patches to ~/Downloads/code-guardian/output/ if no GitHub token."""
        import tempfile, shutil
        out_dir = os.path.join(os.path.expanduser("~"),
                               "Downloads", "code-guardian", "output")
        os.makedirs(out_dir, exist_ok=True)

        for rel_path, content in state.patched_files.items():
            dest = os.path.join(out_dir, rel_path.replace("/", "_"))
            with open(dest, "w") as f:
                f.write(content)

        report_path = os.path.join(out_dir, "bug_report.json")
        import json
        with open(report_path, "w") as f:
            json.dump(state.bug_report, f, indent=2)

        state.pr_url = f"file://{out_dir}  (no GITHUB_TOKEN set — patches saved locally)"
        return state

    def _repo_name(self, url: str) -> str:
        """Extract 'owner/repo' from a GitHub URL."""
        url = url.rstrip("/").rstrip(".git")
        parts = url.split("/")
        return f"{parts[-2]}/{parts[-1]}"
