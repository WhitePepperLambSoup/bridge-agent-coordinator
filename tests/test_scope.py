"""Phase 2.2 tests for allowed/forbidden paths, globs, and scope violations."""

import os
import tempfile
import pytest
from pathlib import Path

from bridgelib.scope import (
    ScopeChecker,
    ScopeViolation,
    normalize_path,
    matches_glob,
    is_within_scope,
)


class TestNormalizePath:
    """Test path normalization."""

    def test_absolute_path(self):
        # normalize_path consistently uses forward slashes.
        result = normalize_path("/tmp/test/file.py")
        assert result == "/tmp/test/file.py"

    def test_relative_path(self):
        result = normalize_path("src/auth/service.py")
        assert "src" in result

    def test_traversal_blocked(self):
        """Remove .. when enough parent directories can be resolved."""
        result = normalize_path("src/sub/../../etc/passwd")
        assert ".." not in result
        assert result == "etc/passwd"

    def test_windows_separators(self):
        result = normalize_path("src\\auth\\service.py")
        assert "\\" not in result or "/" in result


class TestGlobMatching:
    """Test glob pattern matching."""

    def test_exact_match(self):
        assert matches_glob("src/auth/service.py", "src/auth/service.py")

    def test_wildcard_match(self):
        assert matches_glob("src/auth/service.py", "src/auth/*.py")
        assert not matches_glob("src/auth/service.py", "tests/*.py")

    def test_deep_wildcard(self):
        assert matches_glob("src/auth/sub/deep/file.py", "src/auth/**")
        assert matches_glob("src/auth/file.py", "src/auth/**")
        assert not matches_glob("tests/file.py", "src/auth/**")

    def test_question_mark(self):
        assert matches_glob("file1.py", "file?.py")
        assert not matches_glob("file10.py", "file?.py")

    def test_character_class(self):
        assert matches_glob("file_a.py", "file_[a-z].py")
        assert not matches_glob("file_1.py", "file_[a-z].py")


class TestScopeChecker:
    """Test the scope checker."""

    def test_allowed_path_passes(self):
        checker = ScopeChecker(
            allowed=["src/auth/**", "tests/auth/**"],
            forbidden=[".bridge/**", ".env"],
        )
        violations = checker.check("src/auth/service.py")
        assert len(violations) == 0

    def test_forbidden_path_blocked(self):
        checker = ScopeChecker(
            allowed=["src/**"],
            forbidden=[".bridge/**"],
        )
        violations = checker.check(".bridge/runtime/bridge.db")
        assert len(violations) > 0
        assert any("forbidden" in v.reason.lower() for v in violations)

    def test_not_in_allowed_blocked(self):
        checker = ScopeChecker(
            allowed=["src/auth/**"],
            forbidden=[],
        )
        violations = checker.check("deployment/config.yaml")
        assert len(violations) > 0
        assert any("not in allowed" in v.reason.lower() for v in violations)

    def test_exact_forbidden_overrides_allowed(self):
        """Let an exact forbidden path override a wildcard allowance."""
        checker = ScopeChecker(
            allowed=["src/**"],
            forbidden=["src/secrets/**"],
        )
        violations = checker.check("src/secrets/key.env")
        assert len(violations) > 0

    def test_multiple_files_batch_check(self):
        checker = ScopeChecker(
            allowed=["src/**", "tests/**"],
            forbidden=[".bridge/**", "secrets/**"],
        )
        files = [
            "src/main.py",
            "tests/test_main.py",
            ".bridge/runtime/db",
            "docs/readme.md",
        ]
        results = checker.check_batch(files)
        assert not results["src/main.py"]       # Passes.
        assert not results["tests/test_main.py"]  # Passes.
        assert results[".bridge/runtime/db"]     # Forbidden.
        assert results["docs/readme.md"]         # Outside the allowed scope.

    def test_scope_violation_str(self):
        v = ScopeViolation("src/secrets/key.env", "Path matches forbidden pattern: src/secrets/**")
        assert "src/secrets/key.env" in str(v)
        assert "forbidden" in str(v).lower()


class TestIsWithinScope:
    """Test the is_within_scope convenience function."""

    def test_within_allowed_only(self):
        assert is_within_scope("src/main.py", allowed=["src/**"])

    def test_forbidden_blocks(self):
        assert not is_within_scope("secrets/key.env", allowed=["**"], forbidden=["secrets/**"])

    def test_not_allowed(self):
        assert not is_within_scope("docs/readme.md", allowed=["src/**"])
