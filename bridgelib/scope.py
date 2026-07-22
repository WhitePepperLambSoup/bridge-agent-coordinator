"""Bridge 路径范围检查 — 允许/禁止路径、glob 匹配、越界检测。

设计参考：docs/bridge-design/06-git-worktree-and-conflicts.md §文件与全局资源租约
"""

import os
import re
import fnmatch
from dataclasses import dataclass


class ScopeViolation(Exception):
    """范围违规"""
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


# ── Path Normalization ────────────────────────────────────

def normalize_path(path: str) -> str:
    """规范化路径。保留绝对路径前缀，移除 ..，统一分隔符。"""
    is_abs = os.path.isabs(path) or path.startswith("/")
    normalized = os.path.normpath(path)
    normalized = normalized.replace("\\", "/")
    if not is_abs and normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


# ── Glob Matching ─────────────────────────────────────────

def _glob_to_regex(pattern: str) -> re.Pattern:
    """将 glob 模式转换为正则表达式。"""
    parts = pattern.replace("\\", "/").split("/")
    regex_parts = []
    for i, part in enumerate(parts):
        if part == "**":
            if i == len(parts) - 1:
                regex_parts.append(r".*")            # trailing **
            else:
                regex_parts.append(r"(?:.*/)?" )     # middle **
        else:
            translated = fnmatch.translate(part)
            # Strip suffix — fnmatch uses \Z on 3.11/3.12, \z on 3.13+
            for suffix in ("\\z", "\\Z"):
                if translated.endswith(suffix):
                    translated = translated[:-len(suffix)]
                    break
            if translated.startswith("(?s:") and translated.endswith(")"):
                translated = translated[4:-1]
            regex_parts.append(translated)
    full_regex = "/".join(regex_parts)
    return re.compile(f"^{full_regex}$", re.IGNORECASE)


def matches_glob(path: str, pattern: str) -> bool:
    """检查路径是否匹配 glob 模式。"""
    norm_path = normalize_path(path)
    norm_pattern = pattern.replace("\\", "/")
    regex = _glob_to_regex(norm_pattern)
    return bool(regex.match(norm_path))


# ── Scope Checker ─────────────────────────────────────────

@dataclass
class ScopeChecker:
    """路径范围检查器"""
    allowed: list[str]
    forbidden: list[str]

    def check(self, path: str) -> list[ScopeViolation]:
        violations = []
        for pattern in self.forbidden:
            if matches_glob(path, pattern):
                violations.append(ScopeViolation(
                    path, f"Path matches forbidden pattern: {pattern}"
                ))
                break
        if not violations:
            allowed_ok = any(matches_glob(path, p) for p in self.allowed)
            if not allowed_ok:
                violations.append(ScopeViolation(
                    path, f"Path is not in allowed scope: {self.allowed}"
                ))
        return violations

    def check_batch(self, paths: list[str]) -> dict[str, list[ScopeViolation]]:
        return {path: self.check(path) for path in paths}


def is_within_scope(path: str, allowed: list[str], forbidden: list[str] | None = None) -> bool:
    checker = ScopeChecker(allowed=allowed, forbidden=forbidden or [])
    return len(checker.check(path)) == 0
