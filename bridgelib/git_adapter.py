"""Bridge GitRepositoryAdapter — 真实 Git 操作封装。

设计参考：docs/bridge-design/10-adapter-interfaces.md §3 + 06 §Git/Worktree
原则：
- 使用参数数组，不拼接 shell 字符串
- 路径规范化并验证范围
- 返回结构化结果
- 所有写操作需操作 ID 和确认上下文
"""

import os
import subprocess
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class GitAdapterError(Exception):
    """Git 操作错误"""
    def __init__(self, message: str, operation: str = "", stderr: str = ""):
        self.operation = operation
        self.stderr = stderr
        super().__init__(message)


@dataclass
class GitStatus:
    """Git 仓库状态快照"""
    is_repo: bool = False
    is_worktree: bool = False  # .git 是文件（worktree）还是目录
    current_branch: str = ""
    head_commit: str = ""
    has_remote: bool = False
    is_dirty: bool = False     # 有未提交修改
    has_untracked: bool = False
    detached_head: bool = False
    error: str = ""


@dataclass
class GitDiffResult:
    """Git diff 结果"""
    files_changed: list[str] = field(default_factory=list)
    diff_summary: str = ""
    insertions: int = 0
    deletions: int = 0
    error: str = ""


@dataclass
class GitOperationResult:
    """Git 操作结果"""
    success: bool = False
    operation: str = ""
    commit: str = ""
    branch: str = ""
    error: str = ""
    stderr: str = ""
    conflict_files: list[str] = field(default_factory=list)


class GitRepositoryAdapter:
    """Git 仓库适配器 — 所有 Git 操作通过参数数组执行。"""

    def __init__(self, repo_path: str):
        self.repo_path = os.path.realpath(repo_path)
        if not os.path.isdir(self.repo_path):
            raise GitAdapterError(f"Not a directory: {self.repo_path}")

    # ═══════════════════════════════════════════════════════
    # 只读检查
    # ═══════════════════════════════════════════════════════

    def check_repo(self) -> GitStatus:
        """检查仓库状态。"""
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

        # 当前分支
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
        """检查 commit 是否存在。"""
        r = self._run(["git", "cat-file", "-e", commit])
        return r.returncode == 0

    def get_diff(self, base_commit: str, target_commit: str = "HEAD") -> GitDiffResult:
        """获取两个 commit 之间的 diff。"""
        result = GitDiffResult()

        # 文件列表
        r = self._run(["git", "diff", "--name-only", base_commit, target_commit])
        if r.returncode == 0:
            result.files_changed = [f for f in r.stdout.strip().split("\n") if f]

        # 统计
        r = self._run(["git", "diff", "--stat", base_commit, target_commit])
        if r.returncode == 0:
            result.diff_summary = r.stdout.strip()

        # 行数统计
        r = self._run(["git", "diff", "--shortstat", base_commit, target_commit])
        if r.returncode == 0 and r.stdout.strip():
            result.diff_summary = r.stdout.strip()

        return result

    def get_branch_base(self, branch: str, target_branch: str = "main") -> str:
        """获取分支的 merge-base。"""
        r = self._run(["git", "merge-base", target_branch, branch])
        if r.returncode == 0:
            return r.stdout.strip()
        return ""

    # ═══════════════════════════════════════════════════════
    # 写操作（需操作 ID 和确认上下文）
    # ═══════════════════════════════════════════════════════

    def create_branch(self, branch_name: str, base: str = "HEAD",
                      operation_id: str = "") -> GitOperationResult:
        """创建分支。"""
        r = self._run(["git", "branch", branch_name, base])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="create_branch",
            branch=branch_name,
            error=r.stderr.strip() if r.returncode != 0 else "",
        )

    def cherry_pick(self, commit: str, operation_id: str = "",
                    allow_empty: bool = False) -> GitOperationResult:
        """Cherry-pick 一个 commit。"""
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
        """中止 cherry-pick。"""
        r = self._run(["git", "cherry-pick", "--abort"])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="abort_cherry_pick",
        )

    def merge_branch(self, branch: str, operation_id: str = "",
                     no_ff: bool = False) -> GitOperationResult:
        """合并分支。"""
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
        """中止合并。"""
        r = self._run(["git", "merge", "--abort"])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="abort_merge",
        )

    def revert_commit(self, commit: str, operation_id: str = "") -> GitOperationResult:
        """Revert 一个已合并的 commit（创建新提交，不重写历史）。"""
        r = self._run(["git", "revert", "--no-edit", commit])
        return GitOperationResult(
            success=(r.returncode == 0),
            operation="revert",
            commit=commit,
            error=r.stderr.strip() if r.returncode != 0 else "",
        )

    def checkout_branch(self, branch: str, operation_id: str = "") -> GitOperationResult:
        """切换分支。若工作区有未提交修改则拒绝。"""
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
        """创建 worktree。路径必须在允许范围内。"""
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
                        operation_id: str = "") -> GitOperationResult:
        """删除 worktree。force=False 时拒绝删除有未提交修改的。"""
        # 安全检查：确保路径在合理范围内
        abs_path = os.path.realpath(path)
        if not os.path.exists(abs_path):
            return GitOperationResult(
                success=False, operation="remove_worktree",
                error=f"Path does not exist: {path}"
            )

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
        """列出所有 worktree。"""
        r = self._run(["git", "worktree", "list", "--porcelain"])
        if r.returncode != 0:
            return []
        worktrees = []
        current = {}
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
        """执行 git 命令（参数数组，不拼接 shell）。"""
        try:
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
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
        """获取冲突文件列表。"""
        r = self._run(["git", "diff", "--name-only", "--diff-filter=U"])
        if r.returncode == 0:
            return [f for f in r.stdout.strip().split("\n") if f]
        return []
