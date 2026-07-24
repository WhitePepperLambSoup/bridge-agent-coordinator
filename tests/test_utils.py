"""Phase 0 regression tests for utility functions."""

import os
import tempfile
import pytest
from bridgelib.utils import _sanitize_agent_name, _atomic_write, _check_git_repo


class TestSanitizeAgentName:
    """Test agent name sanitization."""

    def test_normal_name_unchanged(self):
        assert _sanitize_agent_name("GPT") == "GPT"
        assert _sanitize_agent_name("Reasonix") == "Reasonix"
        assert _sanitize_agent_name("Claude") == "Claude"

    def test_special_chars_replaced(self):
        """Replace special characters with hyphens and strip trailing hyphens."""
        result = _sanitize_agent_name("Test Agent!")
        assert result == "Test-Agent"

    def test_path_traversal_blocked(self):
        """Block directory traversal."""
        result = _sanitize_agent_name("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result

    def test_empty_name_defaults(self):
        """Fall back to 'agent' for an empty sanitized name."""
        result = _sanitize_agent_name("!@#$%")
        assert result == "agent"

    def test_length_limit(self):
        """Truncate names to 64 characters."""
        long_name = "a" * 100
        result = _sanitize_agent_name(long_name)
        assert len(result) <= 64

    def test_chinese_name_sanitized(self):
        """Safely replace Chinese names and fall back to 'agent' when empty."""
        result = _sanitize_agent_name("中文代理")
        assert result == "agent"


class TestAtomicWrite:
    """Test atomic writes."""

    def test_atomic_write_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.md")
            _atomic_write(path, "Hello World")
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                assert f.read() == "Hello World"

    def test_atomic_write_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.md")
            _atomic_write(path, "First")
            _atomic_write(path, "Second")
            with open(path, "r", encoding="utf-8") as f:
                assert f.read() == "Second"

    def test_atomic_write_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.md")
            _atomic_write(path, "中文内容\nEnglish too")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                assert "中文内容" in content
                assert "English too" in content

    def test_atomic_write_no_partial_on_error(self):
        """Do not leave a partial file after a write failure."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nonexistent", "test.md")
            with pytest.raises(Exception):
                _atomic_write(path, "content")
            # Confirm that no directory or file was created.
            assert not os.path.exists(os.path.join(tmp, "nonexistent"))


class TestGitRepoCheck:
    """Test Git repository checks."""

    def test_non_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, msg = _check_git_repo(tmp)
            assert not ok
            assert "不是 Git 仓库" in msg or "not a git" in msg.lower()

    def test_git_repo_detected(self):
        """Detect a real Git repository."""
        # The project root should be a Git repository.
        ok, msg = _check_git_repo(".")
        assert ok, f"Expected git repo at '.', got: {msg}"
