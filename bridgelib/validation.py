"""Bridge 声明式验证执行器 — 超时、取消、输出截断、证据收集。

设计参考：docs/bridge-design/10-adapter-interfaces.md §4 + 06 §验证门禁
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
    """声明式验证规则"""
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
    """验证执行结果"""
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
    """验证执行器 — 执行声明式检查并收集结果。"""

    def __init__(self, stop_on_failure: bool = False, project_root: str = ""):
        self.stop_on_failure = stop_on_failure
        self.project_root = project_root
        self._cancelled = False
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancelled = True
        self._cancel_event.set()

    def execute_all(self, checks: list[ValidationCheck]) -> list[ValidationResult]:
        """批量执行所有检查。"""
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
    """执行单个验证检查。"""
    start = time.monotonic()
    result = ValidationResult(check_id=check.check_id)

    # ── project_root 安全检查 ────────────────────────────
    resolved_wd = check.working_directory  # default: use as-is
    if project_root:
        wd = check.working_directory
        if wd == ".":
            resolved_wd = os.path.realpath(project_root)
        else:
            resolved_wd = os.path.realpath(os.path.join(project_root, wd))
        abs_root = os.path.realpath(project_root)
        # 使用 commonpath 判断目录归属，禁止字符串前缀
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
            # 创建新进程组，便于后续清理整棵进程树
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
            start_new_session=True if os.name != 'nt' else False,
        )

        # ── 支持取消（清理整棵进程树）───────────────────
        if cancel_event is not None:
            def _kill_on_cancel():
                cancel_event.wait()
                if proc.poll() is None:
                    _kill_process_tree(proc)
            monitor = threading.Thread(target=_kill_on_cancel, daemon=True)
            monitor.start()

        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                timeout=check.timeout_seconds
            )
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            stdout_bytes, stderr_bytes = proc.communicate()
            elapsed = time.monotonic() - start
            result.elapsed_seconds = elapsed
            result.status = ValidationStatus.TIMED_OUT
            result.error_message = f"Timed out after {check.timeout_seconds}s"
            # 即使超时也尝试截断输出
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

        # ── 截断输出（字节级） ───────────────────────────
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        result.stdout, result.stdout_truncated = _truncate(
            stdout_text, check.output_limit_bytes
        )
        result.stderr, result.stderr_truncated = _truncate(
            stderr_text, check.output_limit_bytes
        )

        # ── 检查是否被取消 ──────────────────────────────
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

    # ── 计算证据哈希 ──────────────────────────────────
    if check.evidence_paths:
        result.evidence_hash = _compute_evidence_hash(
            check.evidence_paths, resolved_wd
        )
        # 证据文件缺失时标记为 FAILED（而非静默通过）
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
    """清理整棵进程树（Windows 用 taskkill，Unix 用进程组信号）。"""
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
        # 最后兜底：杀父进程
        try:
            proc.kill()
        except Exception:
            pass


def _compute_evidence_hash(evidence_paths: list[str], working_dir: str) -> str:
    """计算证据文件的 SHA256 哈希。"""
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
    """字节级截断：先编码为 utf-8，按字节截断，再安全解码。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    truncated_bytes = encoded[:limit]
    # 安全解码，忽略末尾不完整字符
    decoded = truncated_bytes.decode("utf-8", errors="ignore")
    return decoded + f"\n... [truncated at {limit} bytes]", True
