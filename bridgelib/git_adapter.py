"""Bridge GitRepositoryAdapter: a wrapper around real Git operations.

Design references: docs/bridge-design/10-adapter-interfaces.md section 3 and 06 Git/Worktree
Principles:
- Use argument arrays instead of concatenating shell strings
- Normalize paths and validate their scope
- Return structured results
- Require an operation ID and confirmation context for all write operations
"""

import os
import subprocess
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class GitAdapterError(Exception):
    """Git operation error."""
    def __init__(self, message: str, operation: str = "", stderr: str = ""):
        self.operation = operation
        self.stderr = stderr
        super().__init__(message)


@dataclass
class GitStatus:
    """Snapshot of Git repository status."""
    is_repo: bool = False
    is_worktree: bool = False  # Whether .git is a file (worktree) rather than a directory
    current_branch: str = ""
    head_commit: str = ""
    has_remote: bool = False
    is_dirty: bool = False     # Whether there are uncommitted changes
    has_untracked: bool = False
    detached_head: bool = False
    error: str = ""


@dataclass
class GitDiffResult:
    """Git diff result."""
    files_changed: list[str] = field(default_factory=list)
    diff_summary: str = ""
    insertions: int = 0
    deletions: int = 0
    error: str = ""


@dataclass
class GitOperationResult:
    """Git operation result."""
    success: bool = False
    operation: str = ""
    commit: str = ""
    branch: str = ""
    error: str = ""
    stderr: str = ""
    conflict_files: list[str] = field(default_factory=list)


class GitRepositoryAdapter:
    """Git repository adapter that executes all Git operations with argument arrays."""

    def __init__(self, repo_path: str):
        self.repo_path = os.path.realpath(repo_path)
        if not os.path.isdir(self.repo_path):
            raise GitAdapterError(f"Not a directory: {self.repo_path}")

    # ═══════════════════════════════════════════════════════
    # Read-only checks
    # ═══════════════════════════════════════════════════════

    def check_repo(self) -> GitStatus:
        """Check repository status."""
        status = GitStatus()

        git_path = os.path.join(self.repo_path, ".git")
        if os.path.isfile(git_path):
            status.is_worktree = True
            status.is_repo = True
        elif os.path.isdir(git_path):
            status.is_repo = True

        if not status.is_repo:
            status.error = f"Not a git repository: {self.repo_path}"
            return status

        # Current branch
        r = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if r.returncode == 0:
            branch = r.stdout.strip()
            status.detached_head = (branch == "HEAD")
            status.current_branch = branch

        # HEAD commit
        r = self._run(["git", "rev-parse", "HEAD"])
        if r.returncode == 0:
            status.head_commit = r.stdout.strip()

        # Remote
        r = self._run(["git", "remote"])
        if r.returncode == 0 and r.stdout.strip():
            status.has_remote = True

        # Dirty
        r = self._run(["git", "status", "--porcelain"])
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if line.strip():
                    if line.startswith("??"):
                        status.has_untracked = True
                    else:
                        status.is_dirty = True

        return status

    def commit_exists(self, commit: str) -> bool:
        """Check whether an object exists and is a commit, not a blob, tree, or tag.

        P0 fix: Use commit^{commit} syntax to ensure the object is a commit
        instead of an arbitrary Git object, so a blob is not mistaken for one.
        """
        r = self._run(["git", "cat-file", "-e", f"{commit}^{{commit}}"])
        return r.returncode == 0

    def verify_commit_strict(self, commit: str) -> bool:
        """Strictly verify that a commit exists and has the correct type; fail closed."""
        r = self._run(["git", "rev-parse", "--verify", f"{commit}^{{commit}}"])
        return r.returncode == 0

    def resolve_commit_sha(self, commit: str, cwd: str | None = None) -> str:
        """Resolve a ref to its canonical full commit SHA, or return an empty string."""
        r = self._run(
            ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
            cwd=cwd,
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    def is_ancestor(self, base_commit: str, target_commit: str) -> bool:
        """Return whether base_commit is reachable from target_commit."""
        r = self._run(["git", "merge-base", "--is-ancestor", base_commit, target_commit])
        return r.returncode == 0

    def verify_commit_provenance(
        self,
        base_commit: str,
        submission_commit: str,
        worktree_path: str = "",
    ) -> tuple[bool, str, str]:
        """Validate a submission SHA against the recorded base and worktree HEAD."""
        base_sha = self.resolve_commit_sha(base_commit)
        submission_sha = self.resolve_commit_sha(submission_commit)
        if not base_sha:
            return False, "base_commit is not a valid commit", ""
        if not submission_sha:
            return False, "submission_commit is not a valid commit", ""
        if submission_commit.lower() != submission_sha:
            return False, "submission_commit must be the canonical full 40-character SHA", submission_sha
        if not self.is_ancestor(base_sha, submission_sha):
            return False, "submission_commit is not based on the recorded base_commit", submission_sha
        if worktree_path:
            worktree_head = self.resolve_commit_sha("HEAD", cwd=worktree_path)
            if worktree_head != submission_sha:
                return False, "submission_commit does not match the current attempt worktree HEAD", submission_sha
        return True, "", submission_sha

    def get_diff(self, base_commit: str, target_commit: str = "HEAD") -> GitDiffResult:
        """Get the diff between two commits."""
        result = GitDiffResult()

        # File list
        r = self._run(["git", "diff", "--name-only", base_commit, target_commit])
        if r.returncode == 0:
            result.files_changed = [f for f in r.stdout.strip().split("\n") if f]
        else:
            result.error = r.stderr.strip() or r.stdout.strip() or "git diff failed"
            return result

        # Statistics
        r = self._run(["git", "diff", "--stat", base_commit, target_commit])
        if r.returncode == 0:
            result.diff_summary = r.stdout.strip()

        # Line statistics
        r = self._run(["git", "diff", "--shortstat", base_commit, target_commit])
        if r.returncode == 0 and r.stdout.strip():
            result.diff_summary = r.stdout.strip()

        return result

    def get_branch_base(self, branch: str, target_branch: str = "main") -> str:
        """Get the merge base of a branch."""
        r = self._run(["git", "merge-base", target_branch, branch])
        if r.returncode == 0:
            return r.stdout.strip()
        return ""

    # ═══════════════════════════════════════════════════════
    # Write operations (require an operation ID and confirmation context)
    # ═══════════════════════════════════════════════════════

    def create_branch(self, branch_name: str, base: str = "HEAD",
                      operation_id: str = "") -> GitOperationResult:
        """Create a branch."""
        r = self._run(["git", "branch", branch_name, base])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="create_branch",
            branch=branch_name,
            error=r.stderr.strip() if r.returncode != 0 else "",
        )

    def cherry_pick(self, commit: str, operation_id: str = "",
                    allow_empty: bool = False) -> GitOperationResult:
        """Cherry-pick a commit."""
        args = ["git", "cherry-pick"]
        if allow_empty:
            args.append("--allow-empty")
        args.append(commit)
        r = self._run(args)
        result = GitOperationResult(
            success=(r.returncode == 0),
            operation="cherry_pick",
            commit=commit,
            error=r.stderr.strip() if r.returncode != 0 else "",
        )
        if r.returncode != 0 and "CONFLICT" in r.stdout:
            result.conflict_files = self._get_conflict_files()
        return result

    def abort_cherry_pick(self) -> GitOperationResult:
        """Abort a cherry-pick."""
        r = self._run(["git", "cherry-pick", "--abort"])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="abort_cherry_pick",
        )

    def merge_branch(self, branch: str, operation_id: str = "",
                     no_ff: bool = False) -> GitOperationResult:
        """Merge a branch."""
        args = ["git", "merge", branch]
        if no_ff:
            args.append("--no-ff")
        r = self._run(args)
        result = GitOperationResult(
            success=(r.returncode == 0),
            operation="merge",
            branch=branch,
            error=r.stderr.strip() if r.returncode != 0 else "",
        )
        if r.returncode != 0 and "CONFLICT" in r.stdout:
            result.conflict_files = self._get_conflict_files()
        return result

    def abort_merge(self) -> GitOperationResult:
        """Abort a merge."""
        r = self._run(["git", "merge", "--abort"])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="abort_merge",
        )

    def revert_commit(self, commit: str, operation_id: str = "") -> GitOperationResult:
        """Revert a merged commit by creating a new commit without rewriting history."""
        r = self._run(["git", "revert", "--no-edit", commit])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="revert",
            commit=commit,
            error=r.stderr.strip() if r.returncode != 0 else "",
        )

    def checkout_branch(self, branch: str, operation_id: str = "") -> GitOperationResult:
        """Switch branches, refusing when the working tree has uncommitted changes."""
        status = self.check_repo()
        if status.is_dirty:
            return GitOperationResult(
                success=False, operation="checkout", branch=branch,
                error="Working directory is dirty — commit or stash changes first"
            )
        r = self._run(["git", "checkout", branch])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="checkout",
            branch=branch,
            error=r.stderr.strip() if r.returncode != 0 else "",
        )

    # ═══════════════════════════════════════════════════════
    # Worktree
    # ═══════════════════════════════════════════════════════

    def create_worktree(self, path: str, branch: str,
                        operation_id: str = "") -> GitOperationResult:
        """Create a worktree; its path must be within the allowed scope."""
        if os.path.exists(path):
            return GitOperationResult(
                success=False, operation="create_worktree",
                error=f"Worktree path already exists: {path}"
            )
        r = self._run(["git", "worktree", "add", path, branch])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="create_worktree",
            branch=branch,
            error=r.stderr.strip() if r.returncode != 0 else "",
        )

    def remove_worktree(self, path: str, force: bool = False,
                        operation_id: str = "",
                        bridge_owned_paths: set | None = None) -> GitOperationResult:
        """Remove a worktree.

        P1 fix:
        - Verify that the path is in bridge_owned_paths, the set of Bridge-managed paths
          supplied by the coordinator
        - Do not rely solely on list_worktrees, which also lists user-created worktrees
        - Use operation_id to track the operation
        """
        abs_path = os.path.realpath(path)
        if not os.path.exists(abs_path):
            return GitOperationResult(
                success=False, operation="remove_worktree",
                error=f"Path does not exist: {path}"
            )

        # Safety check: verify that the path is a Bridge-managed worktree.
        if bridge_owned_paths is not None:
            bridge_paths_real = {os.path.realpath(p) for p in bridge_owned_paths}
            if abs_path not in bridge_paths_real:
                return GitOperationResult(
                    success=False, operation="remove_worktree",
                    error=f"Path '{path}' is not a Bridge-managed worktree — "
                          f"refusing to remove non-Bridge directory"
                )
        else:
            # Fallback: check whether the path is under the .bridge-worktrees directory.
            if ".bridge-worktrees" not in abs_path:
                return GitOperationResult(
                    success=False, operation="remove_worktree",
                    error=f"Path '{path}' is not under .bridge-worktrees — "
                          f"refusing to remove non-Bridge directory"
                )

        if not operation_id:
            logger.warning("remove_worktree called without operation_id")

        args = ["git", "worktree", "remove"]
        if force:
            args.append("--force")
        args.append(path)
        r = self._run(args)
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="remove_worktree",
            error=r.stderr.strip() if r.returncode != 0 else "",
        )

    def list_worktrees(self) -> list[dict]:
        """List all worktrees."""
        r = self._run(["git", "worktree", "list", "--porcelain"])
        if r.returncode != 0:
            return []
        worktrees = []
        current: dict[str, object] = {}
        for line in r.stdout.strip().split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[17:]
            elif line.startswith("detached"):
                current["detached"] = True
        if current:
            worktrees.append(current)
        return worktrees

    # ═══════════════════════════════════════════════════════
    # Internal
    # ═══════════════════════════════════════════════════════

    def _run(self, args: list[str], timeout: int = 30,
             cwd: str | None = None) -> subprocess.CompletedProcess:
        """Execute a Git command with an argument array, without invoking a shell."""
        try:
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd or self.repo_path,
            )
        except subprocess.TimeoutExpired:
            raise GitAdapterError(
                f"Git command timed out after {timeout}s: {' '.join(args)}",
                operation=args[1] if len(args) > 1 else "",
            )
        except FileNotFoundError:
            raise GitAdapterError(
                "Git executable not found. Please install Git 2.40+.",
                operation=args[0],
            )

    def _get_conflict_files(self) -> list[str]:
        """Get the list of files with conflicts."""
        r = self._run(["git", "diff", "--name-only", "--diff-filter=U"])
        if r.returncode == 0:
            return [f for f in r.stdout.strip().split("\n") if f]
        return []
