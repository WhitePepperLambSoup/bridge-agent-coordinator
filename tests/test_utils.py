"""Phase 0 回归测试 — 工具函数模块"""

import os
import tempfile
import pytest
from bridgelib.utils import _sanitize_agent_name, _atomic_write, _check_git_repo


class TestSanitizeAgentName:
    """Agent 名称消毒测试"""

    def test_normal_name_unchanged(self):
        assert _sanitize_agent_name("GPT") == "GPT"
        assert _sanitize_agent_name("Reasonix") == "Reasonix"
        assert _sanitize_agent_name("Claude") == "Claude"

    def test_special_chars_replaced(self):
        """特殊字符替换为连字符（末尾连字符被 strip 去除）"""
        result = _sanitize_agent_name("Test Agent!")
        assert result == "Test-Agent"

    def test_path_traversal_blocked(self):
        """目录穿越被阻止"""
        result = _sanitize_agent_name("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result

    def test_empty_name_defaults(self):
        """空名称回退到 'agent'"""
        result = _sanitize_agent_name("!@#$%")
        assert result == "agent"

    def test_length_limit(self):
        """名称长度被截断到 64 字符"""
        long_name = "a" * 100
        result = _sanitize_agent_name(long_name)
        assert len(result) <= 64

    def test_chinese_name_sanitized(self):
        """中文名称被安全处理（全部替换后若为空，回退到 'agent'）"""
        result = _sanitize_agent_name("中文代理")
        assert result == "agent"


class TestAtomicWrite:
    """原子写入测试"""

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
        """写入失败时不留下半成品"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nonexistent", "test.md")
            with pytest.raises(Exception):
                _atomic_write(path, "content")
            # 确认没有创建目录或文件
            assert not os.path.exists(os.path.join(tmp, "nonexistent"))


class TestGitRepoCheck:
    """Git 仓库检查测试"""

    def test_non_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, msg = _check_git_repo(tmp)
            assert not ok
            assert "不是 Git 仓库" in msg or "not a git" in msg.lower()

    def test_git_repo_detected(self):
        """在真实 Git 仓库中检测"""
        # 项目根目录应该是 Git 仓库
        ok, msg = _check_git_repo(".")
        assert ok, f"Expected git repo at '.', got: {msg}"
