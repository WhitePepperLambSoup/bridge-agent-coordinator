"""Phase 2/3 测试 — GitAdapter + SafetyPolicy + FileWatcher"""

import pytest
import tempfile
import os

from bridgelib.git_adapter import GitRepositoryAdapter, GitAdapterError
from bridgelib.safety import (
    SafetyPolicy, ConfirmationMode, ActionPolicy,
    HARD_FLOOR_ACTIONS, create_strict_policy, create_balanced_policy, create_expert_policy,
)
from bridgelib.file_watcher import FileWatcher, ReceiptWatcher


class TestGitAdapter:
    """GitRepositoryAdapter 测试（在真实 Git 仓库中运行）"""

    @pytest.fixture
    def git_repo(self):
        """创建临时 Git 仓库"""
        import subprocess
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", d], capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "-C", d, "config", "user.name", "Test"], capture_output=True)
        # 创建初始 commit
        with open(os.path.join(d, "README.md"), "w") as f:
            f.write("# Test")
        subprocess.run(["git", "-C", d, "add", "."], capture_output=True)
        subprocess.run(["git", "-C", d, "commit", "-m", "initial"], capture_output=True)
        yield d
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_check_repo(self, git_repo):
        adapter = GitRepositoryAdapter(git_repo)
        status = adapter.check_repo()
        assert status.is_repo
        assert status.head_commit
        assert not status.is_dirty

    def test_commit_exists(self, git_repo):
        adapter = GitRepositoryAdapter(git_repo)
        status = adapter.check_repo()
        assert adapter.commit_exists(status.head_commit)
        assert not adapter.commit_exists("deadbeef" * 5)

    def test_get_diff(self, git_repo):
        import subprocess
        adapter = GitRepositoryAdapter(git_repo)
        base = adapter.check_repo().head_commit
        # 创建第二个 commit
        with open(os.path.join(git_repo, "file.txt"), "w") as f:
            f.write("content")
        subprocess.run(["git", "-C", git_repo, "add", "."], capture_output=True)
        subprocess.run(["git", "-C", git_repo, "commit", "-m", "second"], capture_output=True)
        diff = adapter.get_diff(base, "HEAD")
        assert "file.txt" in diff.files_changed

    def test_create_branch(self, git_repo):
        adapter = GitRepositoryAdapter(git_repo)
        result = adapter.create_branch("test-branch")
        assert result.success

    def test_non_repo_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with pytest.raises(GitAdapterError):
                adapter = GitRepositoryAdapter(os.path.join(d, "nonexistent"))


class TestSafetyPolicy:
    """SafetyPolicy 测试"""

    def test_balanced_default(self):
        sp = create_balanced_policy()
        # 低风险操作可自动
        assert sp.can_automate("create_worktree")
        assert sp.can_automate("run_approved_checks")
        # 高风险操作需确认
        assert sp.requires_confirmation("merge_high_risk")
        assert sp.requires_confirmation("revert_merged_commit")

    def test_strict_mode(self):
        sp = create_strict_policy()
        # Strict 模式下几乎所有操作都需确认
        assert sp.requires_confirmation("create_worktree")

    def test_expert_mode(self):
        sp = create_expert_policy()
        # Expert 模式下只有 ALWAYS_CONFIRM 和 DISABLED 需确认
        assert sp.can_automate("task_assignment")
        assert sp.requires_confirmation("merge_high_risk")

    def test_hard_floor_cannot_be_disabled(self):
        sp = create_expert_policy()
        for action in HARD_FLOOR_ACTIONS:
            assert sp.is_hard_floor(action)
            # 硬底线始终需要确认
            assert sp.requires_confirmation(action)
            # 硬底线不可自动化
            assert not sp.can_automate(action)

    def test_override_normal_action(self):
        sp = create_balanced_policy()
        sp.set_override("task_assignment", ActionPolicy.ALWAYS_CONFIRM)
        assert sp.requires_confirmation("task_assignment")

    def test_override_hard_floor_rejected(self):
        sp = create_balanced_policy()
        with pytest.raises(ValueError, match="Cannot override hard floor"):
            sp.set_override("force_push", ActionPolicy.AUTO)

    def test_mode_switch(self):
        sp = create_balanced_policy()
        sp.mode = ConfirmationMode.STRICT
        assert sp.requires_confirmation("create_worktree")


class TestFileWatcher:
    """FileWatcher 测试"""

    def test_watch_and_detect_stable(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "test.txt")
            with open(path, "w") as f:
                f.write("initial")
            
            fw = FileWatcher(stability_ms=100, poll_interval_ms=50)
            fw.watch(path)
            
            stable_files = []
            fw.on_stable(lambda wf: stable_files.append(wf))
            fw.start()
            time.sleep(0.5)  # 等待稳定
            fw.stop()
            
            # 文件从初始就存在且未修改，应检测为稳定
            assert len(stable_files) >= 1

    def test_watch_nonexistent_file(self):
        with tempfile.TemporaryDirectory() as d:
            fw = FileWatcher(stability_ms=100)
            fw.watch(os.path.join(d, "nonexistent.txt"))
            stable = fw.scan_now()
            assert len(stable) == 0  # 不存在的文件不视为稳定

    def test_unwatch(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "test.txt")
            with open(path, "w") as f:
                f.write("data")
            fw = FileWatcher(stability_ms=100)
            fw.watch(path)
            fw.unwatch(path)
            stable = fw.scan_now()
            assert len(stable) == 0
