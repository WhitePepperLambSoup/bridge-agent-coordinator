"""Bridge declarative validation executor with timeouts, cancellation, and evidence.

Design references: docs/bridge-design/10-adapter-interfaces.md, section 4,
and 06, validation gates
"""

import subprocess
import threading
import time
import os
import hashlib
import signal
from dataclasses import dataclass, field
from enum import Enum


class ValidationStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ValidationError(Exception):
    pass


# ── Validation Check ──────────────────────────────────────

@dataclass
class ValidationCheck:
    """Declarative validation rule."""
    check_id: str
    executable: str
    args: list[str] = field(default_factory=list)
    display_name: str = ""
    working_directory: str = "."
    timeout_seconds: int = 300
    output_limit_bytes: int = 1_048_576    # 1 MB
    required: bool = True
    risk_level: str = "low"
    evidence_paths: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.check_id

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "display_name": self.display_name,
            "executable": self.executable,
            "args": self.args,
            "working_directory": self.working_directory,
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
            "required": self.required,
            "risk_level": self.risk_level,
            "evidence_paths": self.evidence_paths,
        }


# ── Validation Result ─────────────────────────────────────

@dataclass
class ValidationResult:
    """Validation execution result."""
    check_id: str
    status: ValidationStatus = ValidationStatus.PENDING
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    elapsed_seconds: float = 0.0
    evidence_hash: str = ""
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:200],
            "stderr": self.stderr[:200],
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "evidence_hash": self.evidence_hash,
            "error_message": self.error_message,
        }


# ── Validation Executor ───────────────────────────────────

class ValidationExecutor:
    """Execute declarative checks and collect their results."""

    def __init__(self, stop_on_failure: bool = False, project_root: str = ""):
        self.stop_on_failure = stop_on_failure
        self.project_root = project_root
        self._cancelled = False
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancelled = True
        self._cancel_event.set()

    def execute_all(self, checks: list[ValidationCheck]) -> list[ValidationResult]:
        """Execute all checks as a batch.

        P1 fix: scope the cancellation listener thread with an Event instead of
        creating an independent listener thread for every check.
        """
        results = []
        stop = False
        for check in checks:
            if self._cancelled:
                results.append(ValidationResult(
                    check_id=check.check_id,
                    status=ValidationStatus.CANCELLED,
                ))
                continue

            if stop:
                results.append(ValidationResult(
                    check_id=check.check_id,
                    status=ValidationStatus.SKIPPED,
                ))
                continue

            result = run_check(check, project_root=self.project_root,
                               cancel_event=self._cancel_event)
            results.append(result)

            if self.stop_on_failure and result.status == ValidationStatus.FAILED and check.required:
                stop = True

        return results


def run_check(check: ValidationCheck, project_root: str = "",
              cancel_event: threading.Event | None = None) -> ValidationResult:
    """Execute a single validation check."""
    start = time.monotonic()
    result = ValidationResult(check_id=check.check_id)

    # ── project_root safety check ──────────────────────────
    resolved_wd = check.working_directory  # default: use as-is
    if project_root:
        wd = check.working_directory
        if wd == ".":
            resolved_wd = os.path.realpath(project_root)
        else:
            resolved_wd = os.path.realpath(os.path.join(project_root, wd))
        abs_root = os.path.realpath(project_root)
        # Use commonpath for containment instead of a string prefix
        try:
            common = os.path.commonpath([resolved_wd, abs_root])
        except ValueError:
            common = ""
        if os.path.normcase(common) != os.path.normcase(abs_root):
            result.status = ValidationStatus.ERROR
            result.elapsed_seconds = time.monotonic() - start
            result.error_message = (
                f"working_directory '{wd}' resolves outside project_root "
                f"'{project_root}' (resolved to '{resolved_wd}')"
            )
            return result

    try:
        proc = subprocess.Popen(
            [check.executable] + check.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=resolved_wd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
            start_new_session=True if os.name != 'nt' else False,
        )

        # ── Cancellation with a per-check done event for listener cleanup ──
        check_done = threading.Event()
        monitor = None
        if cancel_event is not None:
            def _kill_on_cancel(p=proc, ev=cancel_event, done=check_done):
                # Wait for cancellation or check completion, whichever comes first
                while not done.is_set():
                    if ev.wait(timeout=0.5):
                        if p.poll() is None:
                            _kill_process_tree(p)
                        return
            monitor = threading.Thread(target=_kill_on_cancel, daemon=True)
            monitor.start()

        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                timeout=check.timeout_seconds
            )
            # Notify the listener that the check is complete so it can exit
            check_done.set()
        except subprocess.TimeoutExpired:
            check_done.set()  # Notify the listener thread
            _kill_process_tree(proc)
            stdout_bytes, stderr_bytes = proc.communicate()
            elapsed = time.monotonic() - start
            result.elapsed_seconds = elapsed
            result.status = ValidationStatus.TIMED_OUT
            result.error_message = f"Timed out after {check.timeout_seconds}s"
            # Truncate output even after a timeout
            result.stdout, result.stdout_truncated = _truncate(
                stdout_bytes.decode("utf-8", errors="replace"),
                check.output_limit_bytes,
            )
            result.stderr, result.stderr_truncated = _truncate(
                stderr_bytes.decode("utf-8", errors="replace"),
                check.output_limit_bytes,
            )
            return result

        elapsed = time.monotonic() - start
        result.elapsed_seconds = elapsed
        result.exit_code = proc.returncode

        # ── Truncate output by byte count ──────────────────
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        result.stdout, result.stdout_truncated = _truncate(
            stdout_text, check.output_limit_bytes
        )
        result.stderr, result.stderr_truncated = _truncate(
            stderr_text, check.output_limit_bytes
        )

        # ── Check for cancellation ─────────────────────────
        if cancel_event is not None and cancel_event.is_set():
            result.status = ValidationStatus.CANCELLED
        elif proc.returncode == 0:
            result.status = ValidationStatus.PASSED
        else:
            result.status = ValidationStatus.FAILED

    except FileNotFoundError:
        result.status = ValidationStatus.ERROR
        result.elapsed_seconds = time.monotonic() - start
        result.error_message = f"Executable not found: {check.executable}"

    except Exception as e:
        result.status = ValidationStatus.ERROR
        result.elapsed_seconds = time.monotonic() - start
        result.error_message = str(e)

    # ── Compute the evidence hash ──────────────────────────
    if check.evidence_paths:
        result.evidence_hash = _compute_evidence_hash(
            check.evidence_paths, resolved_wd
        )
        # Mark missing evidence as FAILED instead of silently passing
        missing_evidence = False
        for ep in check.evidence_paths:
            path = os.path.join(resolved_wd, ep)
            if not os.path.isfile(path):
                missing_evidence = True
                break
        if missing_evidence and check.required:
            result.status = ValidationStatus.FAILED
            if not result.error_message:
                result.error_message = f"Required evidence files missing"

    return result


def _kill_process_tree(proc: subprocess.Popen):
    """Kill the process tree using taskkill on Windows or a process-group signal on Unix."""
    if proc.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        # Last resort: kill the parent process
        try:
            proc.kill()
        except Exception:
            pass


def _compute_evidence_hash(evidence_paths: list[str], working_dir: str) -> str:
    """Compute the SHA256 hash of evidence files."""
    if not evidence_paths:
        return ""
    h = hashlib.sha256()
    for ep in evidence_paths:
        path = os.path.join(working_dir, ep) if not os.path.isabs(ep) else ep
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        h.update(chunk)
            except OSError:
                h.update(f"ERROR: cannot read {ep}".encode())
        else:
            h.update(f"MISSING: {ep}".encode())
    return h.hexdigest()


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Truncate by byte count using UTF-8 encoding and safe decoding."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    truncated_bytes = encoded[:limit]
    # Decode safely, ignoring an incomplete trailing character
    decoded = truncated_bytes.decode("utf-8", errors="ignore")
    return decoded + f"\n... [truncated at {limit} bytes]", True
