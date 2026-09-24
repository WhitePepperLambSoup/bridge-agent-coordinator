"""AI Assistant module for Bridge Coordinator.

Provides:
1. AI Pre-Review: automated inspection of Git diffs against acceptance criteria,
   security rules, scope constraints, and best practices before formal review gate.
2. Auto-Fix Diagnosis: automated root-cause analysis and retry prompt generation
   when test/validation checks fail.
3. Merge Conflict Analyzer: 3-way conflict diagnosis and resolution suggestions.

Supports both online LLM inference (via OpenAI-compatible APIs) and
offline deterministic heuristic analysis (rule-based static analysis).
"""

import json
import os
import re
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PreReviewIssue:
    severity: str  # "critical", "warning", "suggestion"
    description: str
    file: str = ""
    line: Optional[int] = None
    suggestion: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AIPreReviewReport:
    verdict: str  # "approved", "changes_requested", "needs_human_attention"
    summary: str
    confidence: float  # 0.0 - 1.0
    scope_compliant: bool
    issues: List[PreReviewIssue] = field(default_factory=list)
    suggested_fixes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "confidence": round(self.confidence, 2),
            "scope_compliant": self.scope_compliant,
            "issues": [i.to_dict() for i in self.issues],
            "suggested_fixes": self.suggested_fixes,
        }

    def to_markdown(self) -> str:
        icon = "✅" if self.verdict == "approved" else ("⚠️" if self.verdict == "needs_human_attention" else "❌")
        md = [
            f"# AI Pre-Review Report {icon}",
            f"",
            f"- **Verdict**: `{self.verdict}`",
            f"- **Confidence**: {int(self.confidence * 100)}%",
            f"- **Scope Compliant**: {'Yes' if self.scope_compliant else 'No (Out-of-scope files touched)'}",
            f"",
            f"## Summary",
            self.summary,
            "",
        ]
        if self.issues:
            md.append("## Issues Detected")
            for issue in self.issues:
                prefix = "🔴 [CRITICAL]" if issue.severity == "critical" else ("🟡 [WARNING]" if issue.severity == "warning" else "🔵 [SUGGESTION]")
                loc = f" (`{issue.file}`:{issue.line})" if issue.file and issue.line else (f" (`{issue.file}`)" if issue.file else "")
                md.append(f"- {prefix}{loc}: {issue.description}")
                if issue.suggestion:
                    md.append(f"  - *Fix*: {issue.suggestion}")
            md.append("")

        if self.suggested_fixes:
            md.append("## Actionable Suggestions")
            for fix in self.suggested_fixes:
                md.append(f"1. {fix}")
            md.append("")

        return "\n".join(md)


@dataclass
class AutoFixDiagnosis:
    root_cause: str
    failure_category: str  # "test_assertion", "syntax_error", "import_error", "runtime_error", "timeout", "unknown"
    failing_tests: List[str] = field(default_factory=list)
    suggested_patch: str = ""
    next_attempt_prompt: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        md = [
            f"# Auto-Fix Diagnosis Report",
            f"",
            f"- **Failure Category**: `{self.failure_category}`",
            f"- **Root Cause**: {self.root_cause}",
            f"",
        ]
        if self.failing_tests:
            md.append(f"## Failing Tests ({len(self.failing_tests)})")
            for t in self.failing_tests:
                md.append(f"- `{t}`")
            md.append("")

        if self.suggested_patch:
            md.append("## Suggested Patch / Remedy")
            md.append(f"```diff\n{self.suggested_patch}\n```")
            md.append("")

        if self.next_attempt_prompt:
            md.append("## Recommended Instruction for Next Attempt")
            md.append(f"```markdown\n{self.next_attempt_prompt}\n```")
            md.append("")

        return "\n".join(md)


class AIAssistant:
    """Intelligent Assistant providing AI Pre-Review, Auto-Fix, and Conflict Diagnosis."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        llm_caller: Optional[Callable[[str, str], str]] = None,
    ):
        self.api_key = api_key or os.environ.get("BRIDGE_AI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self.api_base = api_base or os.environ.get("BRIDGE_AI_BASE_URL", "https://api.openai.com/v1")
        self.model = model or os.environ.get("BRIDGE_AI_MODEL", "gpt-4o-mini")
        self._llm_caller = llm_caller

    def _call_llm_safe(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Call LLM safely, returning None on failure or if credentials missing."""
        if self._llm_caller:
            try:
                return self._llm_caller(system_prompt, user_prompt)
            except Exception as e:
                logger.warning(f"Custom LLM caller failed: {e}")
                return None

        if not self.api_key or len(self.api_key.strip()) < 8:
            return None

        try:
            from bridgelib.llm import call_llm
            return call_llm(
                api_key=self.api_key,
                api_base=self.api_base,
                model=self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout=25,
            )
        except Exception as e:
            logger.warning(f"LLM call failed, falling back to heuristics: {e}")
            return None

    # ── 1. AI Pre-Review ─────────────────────────────────────

    def pre_review(
        self,
        task: Dict[str, Any],
        diff_text: str,
        receipt: Optional[Dict[str, Any]] = None,
        changed_files: Optional[List[str]] = None,
    ) -> AIPreReviewReport:
        """Analyze a submission diff and task requirements to generate a pre-review report."""
        task_id = task.get("id", "")
        title = task.get("title", "")
        goal = task.get("goal", "")

        try:
            allowed_paths = json.loads(task.get("allowed_paths_json") or "[]")
        except Exception:
            allowed_paths = []
        try:
            forbidden_paths = json.loads(task.get("forbidden_paths_json") or "[]")
        except Exception:
            forbidden_paths = []
        try:
            criteria = json.loads(task.get("acceptance_criteria_json") or "[]")
        except Exception:
            criteria = []

        # 1. First run heuristic static checks
        scope_ok, scope_issues = self._check_scope_compliance(
            changed_files or [], allowed_paths, forbidden_paths
        )
        code_issues = self._heuristic_code_checks(diff_text)
        all_issues = scope_issues + code_issues

        # 2. If LLM is configured, enhance with semantic review
        llm_report = self._llm_pre_review(
            task_id=task_id,
            title=title,
            goal=goal,
            criteria=criteria,
            diff_text=diff_text,
            receipt=receipt,
        )

        if llm_report:
            # Merge LLM findings with heuristic findings
            for iss in llm_report.get("issues", []):
                all_issues.append(
                    PreReviewIssue(
                        severity=iss.get("severity", "warning"),
                        description=iss.get("description", ""),
                        file=iss.get("file", ""),
                        line=iss.get("line"),
                        suggestion=iss.get("suggestion", ""),
                    )
                )
            summary = llm_report.get("summary") or "AI semantic analysis completed."
            verdict = llm_report.get("verdict", "approved")
            confidence = float(llm_report.get("confidence", 0.9))
            suggested_fixes = llm_report.get("suggested_fixes", [])
        else:
            # Deterministic heuristic synthesis
            has_critical = any(i.severity == "critical" for i in all_issues)
            has_warning = any(i.severity == "warning" for i in all_issues)
            if not scope_ok or has_critical:
                verdict = "changes_requested"
                summary = "Critical issues or scope violations detected in diff."
                confidence = 0.95
            elif has_warning:
                verdict = "needs_human_attention"
                summary = "Potential warnings detected in diff. Human review recommended."
                confidence = 0.85
            else:
                verdict = "approved"
                summary = "Pre-review checks passed. Diff conforms to scope and no obvious defects detected."
                confidence = 0.90

            suggested_fixes = [i.suggestion for i in all_issues if i.suggestion]

        return AIPreReviewReport(
            verdict=verdict,
            summary=summary,
            confidence=confidence,
            scope_compliant=scope_ok,
            issues=all_issues,
            suggested_fixes=suggested_fixes,
        )

    def _check_scope_compliance(
        self, changed_files: List[str], allowed: List[str], forbidden: List[str]
    ) -> tuple[bool, List[PreReviewIssue]]:
        from bridgelib.scope import is_within_scope
        issues = []
        is_ok = True
        effective_allowed = allowed if allowed else ["**"]
        for f in changed_files:
            if not is_within_scope(f, effective_allowed, forbidden):
                is_ok = False
                issues.append(
                    PreReviewIssue(
                        severity="critical",
                        file=f,
                        description=f"File '{f}' violates task scope restrictions (allowed: {allowed}, forbidden: {forbidden}).",
                        suggestion="Revert changes to this file or adjust task scope before submission.",
                    )
                )
        return is_ok, issues


    def _heuristic_code_checks(self, diff_text: str) -> List[PreReviewIssue]:
        issues = []
        lines = diff_text.splitlines()
        current_file = ""
        current_line_num = 0

        # Pattern detectors
        hardcoded_secrets = re.compile(r'(api_key|password|secret|token)\s*=\s*["\'][a-zA-Z0-9_\-]{16,}["\']', re.IGNORECASE)
        print_debugging = re.compile(r'^\+\s*print\(["\'](DEBUG|test|here|asdf)["\']\)', re.IGNORECASE)
        todo_markers = re.compile(r'^\+\s*#\s*(TODO|FIXME|XXX)', re.IGNORECASE)
        merge_conflict_markers = re.compile(r'^\+\s*(<<<<<<<|=======|>>>>>>>)')

        for line in lines:
            if line.startswith("+++ b/"):
                current_file = line[6:]
                current_line_num = 0
                continue
            if line.startswith("@@"):
                m = re.search(r'\+(\d+)', line)
                if m:
                    current_line_num = int(m.group(1))
                continue

            if line.startswith("+") and not line.startswith("+++"):
                current_line_num += 1

                # Conflict markers
                if merge_conflict_markers.search(line):
                    issues.append(
                        PreReviewIssue(
                            severity="critical",
                            file=current_file,
                            line=current_line_num,
                            description="Unresolved Git merge conflict marker detected in code.",
                            suggestion="Resolve git conflict markers before submitting.",
                        )
                    )

                # Secrets
                if hardcoded_secrets.search(line):
                    is_test_code = "test" in current_file.lower() or current_file.startswith("tests/")
                    severity = "warning" if is_test_code else "critical"
                    desc = (
                        f"Potential test dummy/mock credential in '{current_file}'."
                        if is_test_code else
                        "Potential hardcoded secret or credential exposed in code."
                    )
                    sug = (
                        "Verify that this is only a mock/fixture and not a real production secret."
                        if is_test_code else
                        "Move sensitive credentials to environment variables or secret manager."
                    )
                    issues.append(
                        PreReviewIssue(
                            severity=severity,
                            file=current_file,
                            line=current_line_num,
                            description=desc,
                            suggestion=sug,
                        )
                    )

                # Leftover print
                if print_debugging.search(line):
                    issues.append(
                        PreReviewIssue(
                            severity="warning",
                            file=current_file,
                            line=current_line_num,
                            description="Leftover debug print statement detected.",
                            suggestion="Remove ad-hoc print statements or use proper logging.",
                        )
                    )

                # Unfinished TODO
                if todo_markers.search(line):
                    issues.append(
                        PreReviewIssue(
                            severity="suggestion",
                            file=current_file,
                            line=current_line_num,
                            description="Unfinished TODO/FIXME comment added in submission.",
                            suggestion="Verify whether this TODO should be completed before merge.",
                        )
                    )

        return issues

    def _llm_pre_review(
        self,
        task_id: str,
        title: str,
        goal: str,
        criteria: List[str],
        diff_text: str,
        receipt: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        # Truncate diff if very long to fit LLM window
        truncated_diff = diff_text[:8000] if len(diff_text) > 8000 else diff_text

        system_prompt = (
            "You are a Senior Principal Code Reviewer and Safety Auditor for an autonomous AI software team.\n"
            "Review the Git diff against task goals and acceptance criteria.\n"
            "Output strictly valid JSON with this exact schema:\n"
            "{\n"
            '  "verdict": "approved" | "changes_requested" | "needs_human_attention",\n'
            '  "summary": "Concise summary of review",\n'
            '  "confidence": 0.95,\n'
            '  "issues": [{"severity": "critical"|"warning"|"suggestion", "file": "path", "line": 10, "description": "...", "suggestion": "..."}],\n'
            '  "suggested_fixes": ["action item 1", "action item 2"]\n'
            "}"
        )
        user_prompt = f"""Task ID: {task_id}
Title: {title}
Goal: {goal}
Acceptance Criteria: {json.dumps(criteria)}

Git Diff:
```diff
{truncated_diff}
```
"""
        raw = self._call_llm_safe(system_prompt, user_prompt)
        if not raw:
            return None

        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            if clean.endswith("```"):
                clean = clean[:-3]
        try:
            return json.loads(clean.strip())
        except Exception:
            return None

    # ── 2. Auto-Fix Diagnosis ────────────────────────────────

    def diagnose_failure(
        self,
        task: Dict[str, Any],
        validation_results: List[Dict[str, Any]],
        diff_text: str = "",
    ) -> AutoFixDiagnosis:
        """Analyze failed checks and error output to diagnose root cause and recommend fix."""
        task_id = task.get("id", "")
        title = task.get("title", "")

        # Extract failed check outputs
        failed_checks = [v for v in validation_results if v.get("status") != "passed"]
        error_logs = []
        for fc in failed_checks:
            cid = fc.get("check_id", "unknown")
            output = fc.get("output_summary", "") or fc.get("output", "") or fc.get("error", "")
            error_logs.append(f"--- Check: {cid} ---\n{output}")
        combined_logs = "\n\n".join(error_logs)


        # 1. Deterministic heuristic log analysis
        category, root_cause, failing_tests, quick_patch = self._analyze_test_traceback(combined_logs)

        # 2. Enhance with LLM if available
        llm_fix = self._llm_diagnose_fix(
            task_id=task_id,
            title=title,
            error_logs=combined_logs,
            diff_text=diff_text,
        )
        if llm_fix:
            if llm_fix.get("root_cause"):
                root_cause = llm_fix["root_cause"]
            if llm_fix.get("failure_category"):
                category = llm_fix["failure_category"]
            if llm_fix.get("suggested_patch"):
                quick_patch = llm_fix["suggested_patch"]
            next_prompt = llm_fix.get("next_attempt_prompt", "")
        else:
            next_prompt = self._build_next_attempt_prompt(
                task_id=task_id,
                title=title,
                category=category,
                root_cause=root_cause,
                failing_tests=failing_tests,
                patch=quick_patch,
            )

        return AutoFixDiagnosis(
            root_cause=root_cause,
            failure_category=category,
            failing_tests=failing_tests,
            suggested_patch=quick_patch,
            next_attempt_prompt=next_prompt,
        )

    def _analyze_test_traceback(self, log_text: str) -> tuple[str, str, List[str], str]:
        """Heuristically extract failure type, failed tests, and root cause from logs."""
        failing_tests = []
        category = "unknown"
        root_cause = "Unknown validation failure."
        patch = ""

        # 1. Detect pytest failures: FAILED path::test_name - reason OR FAILED path::test_name
        failed_lines_with_reason = re.findall(r'FAILED\s+([^\s]+)\s*-\s*(.*)', log_text)
        if failed_lines_with_reason:
            for item in failed_lines_with_reason:
                test_name = item[0]
                reason = item[1]
                if test_name not in failing_tests:
                    failing_tests.append(test_name)
                short_name = test_name.split("::")[-1] if "::" in test_name else test_name
                if short_name not in failing_tests:
                    failing_tests.append(short_name)
                root_cause = f"Test '{test_name}' failed: {reason}"
        else:
            failed_lines_bare = re.findall(r'FAILED\s+([^\s:]+::[^\s]+|[^\s]+\.py)', log_text)
            for test_name in failed_lines_bare:
                if test_name not in failing_tests:
                    failing_tests.append(test_name)
                short_name = test_name.split("::")[-1] if "::" in test_name else test_name
                if short_name not in failing_tests:
                    failing_tests.append(short_name)

        # 2. Detect pytest multiline error assertion pattern: E   AssertionError: ... or E   TypeError: ...
        e_lines = re.findall(r'E\s+([A-Za-z]+Error:.*)', log_text)
        if e_lines:
            deepest_err = e_lines[-1].strip()
            if not root_cause or root_cause.startswith("Unknown"):
                root_cause = f"Test failure: {deepest_err}"

        # 3. Detect standard unittest failures: FAIL: test_xxx (tests.test_yyy.TestClass)
        unittest_fails = re.findall(r'FAIL:\s+([^\s]+)\s*\((.*?)\)', log_text)
        for u_item in unittest_fails:
            full_test = f"{u_item[1]}::{u_item[0]}"
            if full_test not in failing_tests:
                failing_tests.append(full_test)
            if u_item[0] not in failing_tests:
                failing_tests.append(u_item[0])
            if not root_cause or root_cause.startswith("Unknown"):
                root_cause = f"Unit test '{u_item[0]}' failed in {u_item[1]}"

        # Category classification
        if "SyntaxError" in log_text:
            category = "syntax_error"
            m = re.search(r'SyntaxError: (.*)', log_text)
            root_cause = f"Python SyntaxError: {m.group(1) if m else 'Invalid syntax'}"
            patch = "# Verify indentation, missing parentheses, colons, or quotes"

        elif "ModuleNotFoundError" in log_text or "ImportError" in log_text:
            category = "import_error"
            m = re.search(r'(ModuleNotFoundError: .*|ImportError: .*)', log_text)
            root_cause = m.group(1) if m else "Missing module or import error"
            patch = "# Ensure required dependency is installed or import path is correct"

        elif "AssertionError" in log_text:
            category = "test_assertion"
            if not root_cause or root_cause.startswith("Unknown"):
                m = re.search(r'AssertionError: (.*)', log_text)
                root_cause = f"AssertionError: {m.group(1) if m else 'Assertion condition evaluated to False'}"

        elif "TypeError" in log_text:
            category = "runtime_error"
            m = re.search(r'TypeError: (.*)', log_text)
            root_cause = f"TypeError: {m.group(1) if m else 'Invalid argument types or counts'}"

        elif "AttributeError" in log_text:
            category = "runtime_error"
            m = re.search(r'AttributeError: (.*)', log_text)
            root_cause = f"AttributeError: {m.group(1) if m else 'Missing attribute or method'}"

        elif any(err in log_text for err in ("ZeroDivisionError", "ValueError", "KeyError", "IndexError", "FileNotFoundError")):
            category = "runtime_error"
            m = re.search(r'((?:ZeroDivisionError|ValueError|KeyError|IndexError|FileNotFoundError): .*)', log_text)
            if m and (not root_cause or root_cause.startswith("Unknown")):
                root_cause = m.group(1)

        elif "timed out" in log_text.lower() or "timeout" in log_text.lower():
            category = "timeout"
            root_cause = "Validation command timed out before completion."


        return category, root_cause, failing_tests, patch

    def _build_next_attempt_prompt(
        self,
        task_id: str,
        title: str,
        category: str,
        root_cause: str,
        failing_tests: List[str],
        patch: str,
    ) -> str:
        lines = [
            f"# Remediation Instruction for Task {task_id}: {title}",
            "",
            f"Previous attempt validation failed with category: **{category}**.",
            f"**Root Cause**: {root_cause}",
            "",
        ]
        if failing_tests:
            lines.append("## Failing Tests to Fix:")
            for t in failing_tests:
                lines.append(f"- `{t}`")
            lines.append("")

        lines.extend([
            "## Required Action Items:",
            "1. Inspect the failing assertions and logs shown above.",
            "2. Make minimal surgical edits to satisfy tests without breaking existing tests.",
            "3. Run local validation checks to confirm 100% PASS before committing.",
            "4. Generate and submit new RECEIPT.md with completed status and updated commit SHA.",
        ])
        return "\n".join(lines)

    def _llm_diagnose_fix(
        self,
        task_id: str,
        title: str,
        error_logs: str,
        diff_text: str,
    ) -> Optional[Dict[str, Any]]:
        truncated_logs = error_logs[:6000] if len(error_logs) > 6000 else error_logs
        truncated_diff = diff_text[:4000] if len(diff_text) > 4000 else diff_text

        system_prompt = (
            "You are an expert Automated Debugging & Self-Healing Agent.\n"
            "Diagnose the root cause of test/validation failure and generate precise remediation instructions.\n"
            "Output strictly valid JSON with this exact schema:\n"
            "{\n"
            '  "failure_category": "test_assertion" | "syntax_error" | "import_error" | "runtime_error" | "timeout" | "unknown",\n'
            '  "root_cause": "Concise explanation of what broke and why",\n'
            '  "suggested_patch": "Code snippet or diff showing fix",\n'
            '  "next_attempt_prompt": "Clear prompt to hand off to implementer agent"\n'
            "}"
        )
        user_prompt = f"""Task: {task_id} - {title}

Recent Git Changes (Diff):
```diff
{truncated_diff}
```

Validation Error Output:
```
{truncated_logs}
```
"""
        raw = self._call_llm_safe(system_prompt, user_prompt)
        if not raw:
            return None

        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            if clean.endswith("```"):
                clean = clean[:-3]
        try:
            return json.loads(clean.strip())
        except Exception:
            return None

    # ── 3. Merge Conflict Analyzer ───────────────────────────

    def diagnose_merge_conflict(
        self,
        task_id: str,
        conflict_files: List[str],
        conflict_diff: str = "",
    ) -> Dict[str, Any]:
        """Analyze 3-way git merge conflicts and propose resolutions."""
        conflict_hunks = conflict_diff.count("<<<<<<<")
        has_imports = "import " in conflict_diff or "from " in conflict_diff
        sections = []
        for file_name in conflict_files:
            file_strategy = "keep_both_and_reorder" if (has_imports and file_name.endswith((".py", ".ts", ".js", ".go"))) else "manual_three_way"
            sections.append({
                "file": file_name,
                "strategy": file_strategy,
                "notes": f"Conflict in {file_name}. Review base vs candidate hunk markers (<<<<<<< HEAD ... ======= ... >>>>>>>).",
            })

        total_hunks = max(conflict_hunks, len(conflict_files))
        return {
            "task_id": task_id,
            "conflict_count": len(conflict_files),
            "conflict_hunks": total_hunks,
            "conflict_files": conflict_files,
            "resolutions": sections,
            "recommended_action": (
                f"Rebase task branch on top of latest target branch, resolve {len(conflict_files)} "
                f"conflicting file(s) ({total_hunks} conflict hunk(s)), re-run validation checks, and re-enqueue merge."
            ),
        }
