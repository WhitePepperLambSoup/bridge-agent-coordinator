"""Phase 1.5 tests - task package protocol (manifest, receipt, artifacts)."""

import json
import tempfile
import os
import pytest
from datetime import datetime, timezone

from bridgelib.protocol import (
    ProtocolVersion,
    Manifest,
    Receipt,
    ReceiptStatus,
    Artifacts,
    TaskPackage,
    validate_manifest,
    validate_receipt,
    validate_artifacts,
    ProtocolError,
    generate_manifest,
    generate_task_package,
    parse_receipt,
)


# ── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def valid_manifest_data():
    return {
        "protocol_version": 1,
        "project_id": "bridge-example",
        "task_id": "TASK-014",
        "attempt": 1,
        "lease_id": "lease-01JEXAMPLE",
        "agent_id": "reasonix-worker",
        "role": "implementer",
        "base_commit": "0123456789abcdef0123456789abcdef01234567",
        "branch": "bridge/reasonix-worker/TASK-014/a1",
        "created_at": "2026-01-01T00:00:00Z",
        "lease_expires_at": "2026-01-01T02:00:00Z",
        "allowed_paths": ["src/protocol/**"],
        "forbidden_paths": [".bridge/**", ".env"],
        "required_outputs": ["receipt", "artifacts", "commit"],
        "title": "Add receipt schema validation",
        "objective": "Reject receipts that do not match the active task.",
        "reviewer_agent_id": "codex-reviewer",
        "acceptance_criteria": [
            "A receipt with wrong attempt number is rejected.",
            "Rejected receipts do not advance task state.",
        ],
        "required_checks": ["unit-tests"],
        "budgets": {
            "token_budget": 50000,
            "time_budget_seconds": 7200,
            "retry_budget": 2,
        },
        "completion": {
            "receipt_path": ".bridge-task/RECEIPT.md",
            "artifacts_path": ".bridge-task/ARTIFACTS.json",
        },
    }


@pytest.fixture
def valid_receipt_markdown():
    return """---
protocol_version: 1
task_id: TASK-014
attempt: 1
lease_id: lease-01JEXAMPLE
agent_id: reasonix-worker
status: completed
submission_commit: fedcba9876543210fedcba9876543210fedcba98
completed_at: 2026-01-01T01:00:00Z
---

# Result

Implemented strict receipt validation.

## Changes

- Added attempt and lease checks.

## Validation

| Check ID | Result | Exit code | Evidence |
|---|---|---:|---|
| unit-tests | passed | 0 | evidence/unit-tests.txt |

## Files Changed

- src/protocol/receipt_validator.py
- tests/protocol/test_receipt_validator.py

## Risks and limitations

- No known limitations.
"""


# ── Manifest Tests ────────────────────────────────────────

class TestManifestValidation:
    def test_valid_manifest(self, valid_manifest_data):
        errors = validate_manifest(valid_manifest_data)
        assert len(errors) == 0

    def test_missing_required_field(self, valid_manifest_data):
        del valid_manifest_data["task_id"]
        errors = validate_manifest(valid_manifest_data)
        assert len(errors) > 0
        assert any("task_id" in e.lower() for e in errors)

    def test_wrong_protocol_version(self, valid_manifest_data):
        valid_manifest_data["protocol_version"] = 99
        errors = validate_manifest(valid_manifest_data)
        assert len(errors) > 0

    def test_invalid_task_id_format(self, valid_manifest_data):
        valid_manifest_data["task_id"] = "bad-format"
        errors = validate_manifest(valid_manifest_data)
        assert len(errors) > 0

    def test_empty_allowed_paths(self, valid_manifest_data):
        valid_manifest_data["allowed_paths"] = []
        errors = validate_manifest(valid_manifest_data)
        assert len(errors) > 0

    def test_missing_required_outputs(self, valid_manifest_data):
        valid_manifest_data["required_outputs"] = []
        errors = validate_manifest(valid_manifest_data)
        assert len(errors) > 0


class TestManifestGeneration:
    def test_generate_manifest(self):
        manifest = generate_manifest(
            task_id="TASK-001",
            attempt=1,
            lease_id="lease-abc",
            agent_id="reasonix-worker",
            role="implementer",
            base_commit="abc123",
            branch="bridge/agent/TASK-001/a1",
            allowed_paths=["src/**"],
            forbidden_paths=[".bridge/**"],
        )
        assert manifest.task_id == "TASK-001"
        assert manifest.protocol_version == ProtocolVersion.V1
        assert manifest.allowed_paths == ["src/**"]

    def test_manifest_to_dict(self):
        manifest = generate_manifest(
            task_id="TASK-001", attempt=1, lease_id="lease-x",
            agent_id="agent-1", role="implementer",
            base_commit="abc", branch="b",
            allowed_paths=["src/**"], forbidden_paths=[".bridge/**"],
        )
        d = manifest.to_dict()
        assert d["task_id"] == "TASK-001"
        assert d["protocol_version"] == 1

    def test_manifest_to_json(self):
        manifest = generate_manifest(
            task_id="TASK-001", attempt=1, lease_id="lease-x",
            agent_id="agent-1", role="implementer",
            base_commit="abc", branch="b",
            allowed_paths=["src/**"], forbidden_paths=[".bridge/**"],
        )
        j = manifest.to_json()
        data = json.loads(j)
        assert data["task_id"] == "TASK-001"


# ── Receipt Tests ─────────────────────────────────────────

class TestReceiptParsing:
    def test_parse_completed_receipt(self, valid_receipt_markdown):
        receipt = parse_receipt(valid_receipt_markdown)
        assert receipt.task_id == "TASK-014"
        assert receipt.attempt == 1
        assert receipt.status == ReceiptStatus.COMPLETED
        assert receipt.agent_id == "reasonix-worker"

    def test_parse_blocked_receipt(self):
        md = """---
protocol_version: 1
task_id: TASK-042
attempt: 2
lease_id: lease-xyz
agent_id: agent-b
status: blocked
submission_commit: ''
completed_at: 2026-01-01T00:00:00Z
---

# Blocked

Waiting for API key access.
"""
        receipt = parse_receipt(md)
        assert receipt.task_id == "TASK-042"
        assert receipt.status == ReceiptStatus.BLOCKED

    def test_parse_failed_receipt(self):
        md = """---
protocol_version: 1
task_id: TASK-099
attempt: 1
lease_id: lease-99
agent_id: agent-c
status: failed
submission_commit: ''
completed_at: 2026-01-01T00:00:00Z
---

# Failed

Tests did not pass.
"""
        receipt = parse_receipt(md)
        assert receipt.status == ReceiptStatus.FAILED

    def test_parse_without_frontmatter_raises(self):
        md = "# Just a heading\n\nNo frontmatter here."
        with pytest.raises(ProtocolError):
            parse_receipt(md)

    def test_parse_unknown_status(self):
        md = """---
protocol_version: 1
task_id: TASK-001
attempt: 1
lease_id: lease-x
agent_id: agent-a
status: unknown_status
submission_commit: ''
completed_at: 2026-01-01T00:00:00Z
---

# Result
"""
        with pytest.raises(ProtocolError):
            parse_receipt(md)

    def test_parse_missing_required_field(self):
        md = """---
protocol_version: 1
task_id: TASK-001
---

# Result
"""
        with pytest.raises(ProtocolError):
            parse_receipt(md)


class TestReceiptValidation:
    def test_cross_check_task_id(self):
        """The receipt task_id must match the manifest."""
        md = """---
protocol_version: 1
task_id: TASK-999
attempt: 1
lease_id: lease-x
agent_id: agent-a
status: completed
submission_commit: abc
completed_at: 2026-01-01T00:00:00Z
---

# Result
"""
        receipt = parse_receipt(md)
        errors = validate_receipt(receipt, expected_task_id="TASK-014")
        assert len(errors) > 0

    def test_cross_check_attempt(self):
        """The receipt attempt must match the current attempt."""
        md = """---
protocol_version: 1
task_id: TASK-014
attempt: 99
lease_id: lease-x
agent_id: agent-a
status: completed
submission_commit: abc
completed_at: 2026-01-01T00:00:00Z
---

# Result
"""
        receipt = parse_receipt(md)
        errors = validate_receipt(receipt, expected_attempt=1)
        assert len(errors) > 0

    def test_cross_check_lease(self):
        """The receipt lease_id must match."""
        md = """---
protocol_version: 1
task_id: TASK-014
attempt: 1
lease_id: wrong-lease
agent_id: agent-a
status: completed
submission_commit: abc
completed_at: 2026-01-01T00:00:00Z
---

# Result
"""
        receipt = parse_receipt(md)
        errors = validate_receipt(receipt, expected_lease_id="lease-01JEXAMPLE")
        assert len(errors) > 0

    def test_valid_receipt_passes_validation(self, valid_receipt_markdown):
        receipt = parse_receipt(valid_receipt_markdown)
        errors = validate_receipt(
            receipt,
            expected_task_id="TASK-014",
            expected_attempt=1,
            expected_lease_id="lease-01JEXAMPLE",
        )
        assert len(errors) == 0


# ── Task Package Tests ────────────────────────────────────

class TestTaskPackage:
    def test_generate_package(self):
        pkg = generate_task_package(
            task_id="TASK-001",
            attempt=1,
            lease_id="lease-x",
            agent_id="agent-a",
            base_commit="abc",
            branch="b",
            title="Test task",
            objective="Do something",
            allowed_paths=["src/**"],
            acceptance_criteria=["AC-1: passes"],
        )
        assert pkg.manifest.task_id == "TASK-001"
        assert "TASK-001" in pkg.prompt
        assert "Test task" in pkg.task_md
        assert "src/**" in pkg.context_md

    def test_package_includes_all_files(self):
        pkg = generate_task_package(
            task_id="TASK-001",
            attempt=1,
            lease_id="lease-x",
            agent_id="agent-a",
            base_commit="abc",
            branch="b",
            title="Test",
            allowed_paths=["src/**"],
        )
        # The task package should contain all nine protocol files.
        files = pkg.to_file_dict()
        assert "manifest.json" in files
        assert "PROMPT.md" in files
        assert "TASK.md" in files
        assert "CONTEXT.md" in files
        assert "CONSTRAINTS.md" in files
        assert "CHECKS.md" in files
        assert "RECEIPT.md" in files
        assert "ARTIFACTS.json" in files
        assert "HEARTBEAT.json" in files

    def test_package_can_be_written(self):
        pkg = generate_task_package(
            task_id="TASK-001", attempt=1, lease_id="lease-x",
            agent_id="agent-a", base_commit="abc", branch="b",
            title="Test", allowed_paths=["src/**"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = os.path.join(tmp, ".bridge-task")
            pkg.write_to(task_dir)
            assert os.path.exists(os.path.join(task_dir, "manifest.json"))
            assert os.path.exists(os.path.join(task_dir, "PROMPT.md"))
            assert os.path.exists(os.path.join(task_dir, "TASK.md"))
            # Verify that manifest.json contains valid content.
            with open(os.path.join(task_dir, "manifest.json"), "r") as f:
                data = json.load(f)
            assert data["task_id"] == "TASK-001"
