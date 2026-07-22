"""Bridge 任务包协议 — manifest, receipt, artifacts 的生成与验证。

设计参考：docs/bridge-design/05-agent-file-protocol.md
"""

import json
import os
import re
import hashlib
import tempfile
import logging
from enum import IntEnum
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


class ProtocolVersion(IntEnum):
    V1 = 1


class ReceiptStatus:
    PROGRESS = "progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"

    _ALL = {PROGRESS, BLOCKED, COMPLETED, FAILED, ABANDONED}

    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._ALL


class ProtocolError(Exception):
    """协议验证错误"""
    pass


# ── Manifest ──────────────────────────────────────────────

@dataclass
class Manifest:
    """任务清单 — Agent 只读。"""
    protocol_version: int = 1
    project_id: str = ""
    task_id: str = ""
    attempt: int = 1
    lease_id: str = ""
    agent_id: str = ""
    role: str = "implementer"
    base_commit: str = ""
    branch: str = ""
    created_at: str = ""
    lease_expires_at: str = ""
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    required_outputs: list[str] = field(default_factory=lambda: ["receipt", "artifacts", "commit"])
    title: str = ""
    objective: str = ""
    reviewer_agent_id: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    required_checks: list[str] = field(default_factory=list)
    budgets: dict = field(default_factory=dict)
    completion: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["protocol_version"] = int(self.protocol_version)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def generate_manifest(**kwargs) -> Manifest:
    """生成任务 manifest。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Manifest(
        protocol_version=ProtocolVersion.V1,
        project_id=kwargs.get("project_id", ""),
        task_id=kwargs.get("task_id", ""),
        attempt=kwargs.get("attempt", 1),
        lease_id=kwargs.get("lease_id", ""),
        agent_id=kwargs.get("agent_id", ""),
        role=kwargs.get("role", "implementer"),
        base_commit=kwargs.get("base_commit", ""),
        branch=kwargs.get("branch", ""),
        created_at=now,
        lease_expires_at=kwargs.get("lease_expires_at", ""),
        allowed_paths=kwargs.get("allowed_paths", []),
        forbidden_paths=kwargs.get("forbidden_paths", [".bridge/**"]),
        required_outputs=kwargs.get("required_outputs", ["receipt", "artifacts", "commit"]),
        title=kwargs.get("title", ""),
        objective=kwargs.get("objective", ""),
        reviewer_agent_id=kwargs.get("reviewer_agent_id", ""),
        acceptance_criteria=kwargs.get("acceptance_criteria", []),
        required_checks=kwargs.get("required_checks", []),
        budgets=kwargs.get("budgets", {}),
        completion=kwargs.get("completion", {
            "receipt_path": ".bridge-task/RECEIPT.md",
            "artifacts_path": ".bridge-task/ARTIFACTS.json",
        }),
    )


def validate_manifest(data: dict) -> list[str]:
    """验证 manifest 数据，返回错误列表（空列表 = 通过）。"""
    errors = []

    # 必填字段检查
    required = [
        "protocol_version", "task_id", "attempt", "lease_id",
        "agent_id", "role", "base_commit", "branch",
        "allowed_paths", "forbidden_paths", "required_outputs",
    ]
    for field in required:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    if errors:
        return errors

    # 协议版本
    if data["protocol_version"] != ProtocolVersion.V1:
        errors.append(f"Unsupported protocol_version: {data['protocol_version']}")

    # task_id 格式：TASK-\d+
    if not re.match(r"^TASK-\d{3,}$", data.get("task_id", "")):
        errors.append(f"Invalid task_id format: {data.get('task_id')}")

    # attempt 必须 > 0
    if not isinstance(data.get("attempt"), int) or data["attempt"] < 1:
        errors.append("attempt must be a positive integer")

    # allowed_paths 不能为空
    if not data.get("allowed_paths"):
        errors.append("allowed_paths must not be empty")

    # required_outputs 不能为空
    if not data.get("required_outputs"):
        errors.append("required_outputs must not be empty")

    # lease_id 必须非空
    if not data.get("lease_id"):
        errors.append("lease_id must not be empty")

    return errors


# ── Receipt ───────────────────────────────────────────────

@dataclass
class Receipt:
    """Agent 回执 — Agent 写入，Bridge 读取。"""
    protocol_version: int = 1
    task_id: str = ""
    attempt: int = 1
    lease_id: str = ""
    agent_id: str = ""
    status: str = ""
    submission_commit: str = ""
    completed_at: str = ""
    body: str = ""

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "lease_id": self.lease_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "submission_commit": self.submission_commit,
            "completed_at": self.completed_at,
        }


def parse_receipt(markdown: str) -> Receipt:
    """解析 RECEIPT.md 文件内容，返回 Receipt 对象。"""
    # 提取 YAML front matter
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", markdown, re.DOTALL)
    if not fm_match:
        raise ProtocolError("RECEIPT_MISSING_FRONTMATTER",
                           "Receipt is missing YAML front matter")

    frontmatter = fm_match.group(1)
    body = fm_match.group(2).strip()

    # 简单 YAML 解析（不依赖第三方库）
    data = {}
    for line in frontmatter.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            data[key] = value

    # 必填字段
    required = ["task_id", "attempt", "lease_id", "agent_id", "status", "protocol_version"]
    for field in required:
        if field not in data:
            raise ProtocolError(
                "RECEIPT_MISSING_FIELD",
                f"Receipt is missing required field: {field}"
            )

    # 验证协议版本
    protocol_ver = int(data.get("protocol_version", 0))
    if protocol_ver != ProtocolVersion.V1:
        raise ProtocolError(
            "RECEIPT_BAD_PROTOCOL",
            f"Unsupported protocol_version: {protocol_ver}"
        )

    # 验证 status
    status = data.get("status", "")
    if not ReceiptStatus.is_valid(status):
        raise ProtocolError(
            "RECEIPT_INVALID_STATUS",
            f"Invalid receipt status: {status}"
        )

    submission_commit = data.get("submission_commit", "")
    # completed 状态必须提供 submission_commit
    if status == ReceiptStatus.COMPLETED and not submission_commit:
        raise ProtocolError(
            "RECEIPT_MISSING_COMMIT",
            "Completed receipt must include submission_commit"
        )

    return Receipt(
        protocol_version=protocol_ver,
        task_id=data["task_id"],
        attempt=int(data["attempt"]),
        lease_id=data["lease_id"],
        agent_id=data["agent_id"],
        status=status,
        submission_commit=submission_commit,
        completed_at=data.get("completed_at", ""),
        body=body,
    )


def validate_receipt(
    receipt: Receipt,
    expected_task_id: str | None = None,
    expected_attempt: int | None = None,
    expected_lease_id: str | None = None,
    expected_agent_id: str | None = None,
) -> list[str]:
    """交叉验证回执与预期值，返回错误列表。"""
    errors = []

    if expected_task_id and receipt.task_id != expected_task_id:
        errors.append(
            f"Task ID mismatch: expected {expected_task_id}, got {receipt.task_id}"
        )

    if expected_attempt is not None and receipt.attempt != expected_attempt:
        errors.append(
            f"Attempt mismatch: expected {expected_attempt}, got {receipt.attempt}"
        )

    if expected_lease_id and receipt.lease_id != expected_lease_id:
        errors.append(
            f"Lease ID mismatch: expected {expected_lease_id}, got {receipt.lease_id}"
        )

    if expected_agent_id and receipt.agent_id != expected_agent_id:
        errors.append(
            f"Agent ID mismatch: expected {expected_agent_id}, got {receipt.agent_id}"
        )

    return errors


# ── Artifacts ─────────────────────────────────────────────

@dataclass
class Artifacts:
    """产物清单 — Agent 写入，Bridge 读取。"""
    protocol_version: int = 1
    task_id: str = ""
    attempt: int = 1
    agent_id: str = ""
    base_commit: str = ""
    submission_commit: str = ""
    changed_files: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    evidence_files: list[str] = field(default_factory=list)
    new_dependencies: list[str] = field(default_factory=list)
    migrations: list[str] = field(default_factory=list)
    known_failures: list[str] = field(default_factory=list)
    generated_at: str = ""


def validate_artifacts(data: dict, expected_task_id: str = "",
                      expected_attempt: int = 0, expected_agent_id: str = "",
                      expected_base_commit: str = "") -> list[str]:
    """验证 ARTIFACTS.json，返回错误列表。"""
    errors = []

    if data.get("protocol_version") != 1:
        errors.append("Unsupported protocol_version")

    if expected_task_id and data.get("task_id") != expected_task_id:
        errors.append(f"Task ID mismatch in artifacts: expected {expected_task_id}")

    if expected_attempt and data.get("attempt") != expected_attempt:
        errors.append(f"Attempt mismatch in artifacts")

    if expected_agent_id and data.get("agent_id") != expected_agent_id:
        errors.append(f"Agent ID mismatch in artifacts")

    if expected_base_commit and data.get("base_commit") != expected_base_commit:
        errors.append(f"Base commit mismatch in artifacts")

    if not data.get("changed_files"):
        errors.append("No changed_files in artifacts")

    # 路径安全检查：不允许 ../ 路径
    for f in data.get("changed_files", []):
        path = f.get("path", "") if isinstance(f, dict) else str(f)
        if ".." in path:
            errors.append(f"Path traversal detected in artifacts: {path}")

    return errors


# ── Task Package ──────────────────────────────────────────

@dataclass
class TaskPackage:
    """完整的任务包 — 包含所有协议文件的内容。"""
    manifest: Manifest
    prompt: str = ""
    task_md: str = ""
    context_md: str = ""
    constraints_md: str = ""
    checks_md: str = ""
    receipt_md: str = ""
    artifacts_json: str = ""
    heartbeat_json: str = ""

    def to_file_dict(self) -> dict[str, str]:
        return {
            "manifest.json": self.manifest.to_json(),
            "PROMPT.md": self.prompt,
            "TASK.md": self.task_md,
            "CONTEXT.md": self.context_md,
            "CONSTRAINTS.md": self.constraints_md,
            "CHECKS.md": self.checks_md,
            "RECEIPT.md": self.receipt_md,
            "ARTIFACTS.json": self.artifacts_json,
            "HEARTBEAT.json": self.heartbeat_json,
        }

    def write_to(self, directory: str):
        """将任务包原子写入目录。检测到已有非空回执时拒绝覆盖（不可绕过）。"""
        os.makedirs(directory, exist_ok=True)

        # 检查是否存在未导入的回执 — 无条件拒绝覆盖
        receipt_path = os.path.join(directory, "RECEIPT.md")
        if os.path.exists(receipt_path):
            with open(receipt_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                raise ProtocolError(
                    "RECEIPT_OVERWRITE_BLOCKED",
                    f"已有非空回执文件 {receipt_path}。"
                    f"请先导入回执后再重新生成任务包。"
                    f"回执内容哈希: {hashlib.sha256(content.encode()).hexdigest()[:16]}"
                )

        for filename, content in self.to_file_dict().items():
            path = os.path.join(directory, filename)
            # 原子写入：先写临时文件，再 rename
            fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise


def generate_task_package(**kwargs) -> TaskPackage:
    """生成完整任务包。"""
    manifest = generate_manifest(**kwargs)

    task_id = kwargs.get("task_id", "TASK-000")
    title = kwargs.get("title", "Untitled Task")
    objective = kwargs.get("objective", "")
    allowed = kwargs.get("allowed_paths", [])
    forbidden = kwargs.get("forbidden_paths", [".bridge/**"])
    acceptance = kwargs.get("acceptance_criteria", [])
    agent_id = kwargs.get("agent_id", "")
    reviewer_id = kwargs.get("reviewer_agent_id", "")
    checks = kwargs.get("required_checks", [])

    allowed_str = "\n".join(f"- `{p}`" for p in allowed)
    forbidden_str = "\n".join(f"- `{p}`" for p in forbidden)
    ac_str = "\n".join(f"- [ ] {ac}" for ac in acceptance)
    checks_str = "\n".join(f"- `{c}`" for c in checks)

    prompt = f"""# Task {task_id}: {title}

You are **{agent_id}** working on this task.

## Quick Start
1. Read `TASK.md` for the objective and acceptance criteria.
2. Read `CONTEXT.md` for background information.
3. Read `CONSTRAINTS.md` for what you can and cannot do.
4. Read `CHECKS.md` for required validation steps.
5. When done, fill out `RECEIPT.md` and `ARTIFACTS.json`, then commit.

## Important
- Only modify files within your allowed paths.
- Do NOT modify `manifest.json` or files outside your scope.
- Commit and push after each atomic change.
"""

    task_md = f"""---
task_id: {task_id}
attempt: {kwargs.get("attempt", 1)}
owner: {agent_id}
reviewer: {reviewer_id}
risk: {kwargs.get("risk", "medium")}
complexity: {kwargs.get("complexity", "medium")}
---

# {title}

## Objective

{objective}

## Scope

Allowed paths:
{allowed_str}

Forbidden paths:
{forbidden_str}

## Acceptance Criteria

{ac_str}

## Non-Goals

- Defined by planner during task creation.
"""

    context_md = f"""# Context for {task_id}

## Base Commit

`{kwargs.get("base_commit", "unknown")}`

## Branch

`{kwargs.get("branch", "unknown")}`

## Allowed Paths

{allowed_str}

## Notes

- This context is generated by Bridge. Do not modify.
- If you need additional context, request it via RECEIPT.md.
"""

    constraints_md = f"""# Constraints for {task_id}

## Allowed

{allowed_str}

## Forbidden

{forbidden_str}

## Git Rules

- Work on branch `{kwargs.get("branch", "unknown")}`
- Commit after each atomic change
- Do NOT push to main/master directly
- Do NOT force push

## Safety Rules

- Do NOT modify manifest.json
- Do NOT modify other agents' task packages
- Do NOT modify `.bridge/` runtime files
- Do NOT include secrets or credentials in commits
"""

    checks_md = f"""# Checks for {task_id}

## Required Checks

{checks_str if checks_str else "- No automated checks configured."}

## How to Run

Each check will be executed independently by Bridge during validation.
Your RECEIPT.md should record your local results, but Bridge will re-run
all checks during the validation gate.

## Evidence

For each check passed, provide the evidence path in ARTIFACTS.json.
"""

    receipt_md = f"""---
protocol_version: 1
task_id: {task_id}
attempt: {kwargs.get("attempt", 1)}
lease_id: {kwargs.get("lease_id", "")}
agent_id: {agent_id}
status: progress
submission_commit: ''
completed_at: ''
---

# Progress Receipt

## Summary

<!-- Write a brief summary of what you accomplished -->

## Acceptance Evidence

<!-- List evidence for each acceptance criterion -->

## Checks

| Check ID | Exit code | Result |
|---|---|---|
| - | - | - |

## Files Changed

<!-- List files you modified -->

## Risks and Limitations

<!-- Any known issues or limitations -->

## Requests for Reviewer

<!-- Questions or concerns for the reviewer -->
"""

    artifacts_json = json.dumps({
        "protocol_version": 1,
        "task_id": task_id,
        "attempt": kwargs.get("attempt", 1),
        "agent_id": agent_id,
        "base_commit": kwargs.get("base_commit", ""),
        "submission_commit": "",
        "changed_files": [],
        "checks": [],
        "evidence_files": [],
        "new_dependencies": [],
        "migrations": [],
        "known_failures": [],
        "generated_at": "",
    }, indent=2)

    heartbeat_json = json.dumps({
        "protocol_version": 1,
        "task_id": task_id,
        "lease_id": kwargs.get("lease_id", ""),
        "agent_id": agent_id,
        "status": "active",
        "progress_pct": 0,
        "current_step": "task package received",
        "updated_at": "",
        "blocker": "",
    }, indent=2)

    return TaskPackage(
        manifest=manifest,
        prompt=prompt,
        task_md=task_md,
        context_md=context_md,
        constraints_md=constraints_md,
        checks_md=checks_md,
        receipt_md=receipt_md,
        artifacts_json=artifacts_json,
        heartbeat_json=heartbeat_json,
    )
