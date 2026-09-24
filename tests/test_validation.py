"""Phase 2.3 tests for the declarative validation executor."""

import os
import json
import tempfile
import pytest

from bridgelib.validation import (
    ValidationCheck,
    ValidationResult,
    ValidationExecutor,
    ValidationStatus,
    ValidationError,
    run_check,
)


class TestValidationCheck:
    """Test validation check definitions."""

    def test_minimal_check(self):
        check = ValidationCheck(
            check_id="unit-tests",
            executable="python",
            args=["-m", "pytest", "-q"],
        )
        assert check.check_id == "unit-tests"
        assert check.required is True     # Required by default.
        assert check.timeout_seconds == 300
        assert check.output_limit_bytes == 1_048_576

    def test_full_check(self):
        check = ValidationCheck(
            check_id="lint",
            display_name="Lint check",
            executable="ruff",
            args=["check", "."],
            working_directory="/tmp",
            timeout_seconds=60,
            output_limit_bytes=100_000,
            required=False,
            risk_level="low",
            evidence_paths=["lint-output.txt"],
        )
        assert check.required is False
        assert check.risk_level == "low"

    def test_to_dict(self):
        check = ValidationCheck(
            check_id="type-check",
            executable="mypy",
            args=["src/"],
            timeout_seconds=120,
        )
        d = check.to_dict()
        assert d["check_id"] == "type-check"
        assert d["executable"] == "mypy"
        assert d["args"] == ["src/"]


class TestValidationResult:
    """Test validation results."""

    def test_passed_result(self):
        result = ValidationResult(
            check_id="unit-tests",
            status=ValidationStatus.PASSED,
            exit_code=0,
            stdout="All tests passed.\n",
            stderr="",
            elapsed_seconds=2.5,
        )
        assert result.status == ValidationStatus.PASSED
        assert result.exit_code == 0
        assert "passed" in result.stdout

    def test_failed_result(self):
        result = ValidationResult(
            check_id="lint",
            status=ValidationStatus.FAILED,
            exit_code=1,
            stdout="",
            stderr="Error: line too long\n",
            elapsed_seconds=0.3,
        )
        assert result.status == ValidationStatus.FAILED
        assert result.exit_code == 1

    def test_timed_out_result(self):
        result = ValidationResult(
            check_id="slow-check",
            status=ValidationStatus.TIMED_OUT,
            exit_code=None,
            elapsed_seconds=300,
        )
        assert result.status == ValidationStatus.TIMED_OUT

    def test_to_dict(self):
        result = ValidationResult(
            check_id="test", status=ValidationStatus.PASSED,
            exit_code=0, elapsed_seconds=1.0,
        )
        d = result.to_dict()
        assert d["check_id"] == "test"
        assert d["status"] == "passed"


class TestValidationExecutor:
    """Test the validation executor."""

    def test_run_passing_check(self):
        """Run a simple command that succeeds."""
        check = ValidationCheck(
            check_id="echo-test",
            executable="python",
            args=["-c", "print('hello')"],
            timeout_seconds=5,
        )
        result = run_check(check)
        assert result.status == ValidationStatus.PASSED
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_run_failing_check(self):
        """Run a command that fails."""
        check = ValidationCheck(
            check_id="fail-test",
            executable="python",
            args=["-c", "import sys; sys.exit(1)"],
            timeout_seconds=5,
        )
        result = run_check(check)
        assert result.status == ValidationStatus.FAILED
        assert result.exit_code == 1

    def test_execute_all(self):
        """Execute checks in a batch."""
        executor = ValidationExecutor()
        checks = [
            ValidationCheck(check_id="c1", executable="python",
                          args=["-c", "print('ok')"], timeout_seconds=5),
            ValidationCheck(check_id="c2", executable="python",
                          args=["-c", "print('also ok')"], timeout_seconds=5),
        ]
        results = executor.execute_all(checks)
        assert len(results) == 2
        assert all(r.status == ValidationStatus.PASSED for r in results)

    def test_execute_all_stops_on_failure(self):
        """Test stop_on_failure mode."""
        executor = ValidationExecutor(stop_on_failure=True)
        checks = [
            ValidationCheck(check_id="fail", executable="python",
                          args=["-c", "import sys; sys.exit(1)"], timeout_seconds=5,
                          required=True),
            ValidationCheck(check_id="never-runs", executable="python",
                          args=["-c", "print('should not run')"], timeout_seconds=5),
        ]
        results = executor.execute_all(checks)
        # The second check should be skipped.
        assert len(results) <= 1 or results[1].status == ValidationStatus.SKIPPED

    def test_output_truncation(self):
        """Truncate oversized output."""
        check = ValidationCheck(
            check_id="big-output",
            executable="python",
            args=["-c", "print('x' * 5000)"],
            output_limit_bytes=100,
            timeout_seconds=5,
        )
        result = run_check(check)
        assert len(result.stdout) <= 200  # Allow some overhead.
        assert result.stdout_truncated

    def test_nonexistent_executable(self):
        """Handle a nonexistent executable."""
        check = ValidationCheck(
            check_id="no-such-exe",
            executable="nonexistent_command_xyz",
            args=[],
            timeout_seconds=5,
        )
        result = run_check(check)
        assert result.status in (ValidationStatus.ERROR, ValidationStatus.FAILED)

    def test_non_required_check_failure_not_blocking(self):
        """Do not block on a failed optional check."""
        check = ValidationCheck(
            check_id="optional-lint",
            executable="python",
            args=["-c", "import sys; sys.exit(1)"],
            timeout_seconds=5,
            required=False,
        )
        result = run_check(check)
        assert result.status == ValidationStatus.FAILED
        # The failed check should remain marked as optional.
        assert not check.required


class TestValidationExecutorIntegration:
    """Integration tests for the validation executor."""

    def test_executor_with_temp_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "test_script.py")
            with open(script, "w") as f:
                f.write("print('integration test passed')")
            check = ValidationCheck(
                check_id="script-test",
                executable="python",
                args=[script],
                working_directory=tmp,
                timeout_seconds=5,
            )
            result = run_check(check)
            assert result.status == ValidationStatus.PASSED
