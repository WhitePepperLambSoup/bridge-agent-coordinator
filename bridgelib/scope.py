"""Bridge path scope checks for allowed paths, forbidden paths, and glob matching.

Design reference: docs/bridge-design/06-git-worktree-and-conflicts.md,
file and global resource leases
"""

import os
import re
import fnmatch
from dataclasses import dataclass


class ScopeViolation(Exception):
    """Path scope violation."""
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


# ── Path Normalization ────────────────────────────────────

def normalize_path(path: str) -> str:
    """Normalize a path while preserving absolute prefixes and unifying separators."""
    is_abs = os.path.isabs(path) or path.startswith("/")
    normalized = os.path.normpath(path)
    normalized = normalized.replace("\\", "/")
    if not is_abs and normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


# ── Glob Matching ─────────────────────────────────────────

def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a glob pattern to a regular expression."""
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
    """Check whether a path matches a glob pattern."""
    norm_path = normalize_path(path)
    norm_pattern = pattern.replace("\\", "/")
    regex = _glob_to_regex(norm_pattern)
    return bool(regex.match(norm_path))


# ── Scope Checker ─────────────────────────────────────────

@dataclass
class ScopeChecker:
    """Path scope checker."""
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
