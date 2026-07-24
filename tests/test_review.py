"""Phase 3.1 tests for the review system."""

import pytest
from datetime import datetime, timezone

from bridgelib.review import (
    ReviewVerdict,
    ReviewRequest,
    ReviewResult,
    ReviewManager,
    ReviewError,
    ReviewPackage,
)


class TestReviewVerdict:
    """Test the review verdict enum."""

    def test_four_verdicts(self):
        verdicts = {
            ReviewVerdict.APPROVED,
            ReviewVerdict.REVISION_REQUIRED,
            ReviewVerdict.BLOCKED,
            ReviewVerdict.ESCALATE,
        }
        assert len(verdicts) == 4

    def test_verdict_values(self):
        assert ReviewVerdict.APPROVED.value == "approved"
        assert ReviewVerdict.REVISION_REQUIRED.value == "revision_required"
        assert ReviewVerdict.BLOCKED.value == "blocked"
        assert ReviewVerdict.ESCALATE.value == "escalate"


class TestReviewPackage:
    """Test the minimal context package provided to a review agent."""

    def test_minimal_package(self):
        pkg = ReviewPackage(
            task_id="TASK-014",
            title="Add receipt validation",
            risk="medium",
            acceptance_criteria=["AC-1: valid receipts accepted"],
            changed_files=["src/receipt.py", "tests/test_receipt.py"],
            implementer_receipt="Completed: added validation logic.",
        )
        assert pkg.task_id == "TASK-014"
        assert len(pkg.acceptance_criteria) == 1
        assert len(pkg.changed_files) == 2

    def test_full_package(self):
        pkg = ReviewPackage(
            task_id="TASK-001",
            title="Login feature",
            objective="Add OAuth login",
            risk="high",
            complexity="medium",
            approved_decisions=["Use OAuth 2.0", "JWT for sessions"],
            acceptance_criteria=["AC-1", "AC-2"],
            changed_files=["src/auth/login.py"],
            diff_summary="+150 -20 lines in auth module",
            check_evidence={"unit-tests": "42 passed"},
            implementer_receipt="All tests pass.",
            known_risks=["Old client compatibility"],
            pending_questions=["Should we support SAML?"],
        )
        assert pkg.risk == "high"
        assert len(pkg.approved_decisions) == 2
        assert pkg.check_evidence is not None


class TestReviewRequest:
    """Test review requests."""

    def test_create_request(self):
        req = ReviewRequest(
            request_id="rev-001",
            task_id="TASK-014",
            reviewer_agent_id="codex-reviewer",
            review_package=ReviewPackage(
                task_id="TASK-014",
                title="Test",
                acceptance_criteria=["AC-1"],
                changed_files=["f.py"],
                implementer_receipt="done",
            ),
        )
        assert req.task_id == "TASK-014"
        assert req.reviewer_agent_id == "codex-reviewer"
        assert req.status == "pending"

    def test_request_to_dict(self):
        req = ReviewRequest(
            request_id="rev-002",
            task_id="TASK-099",
            reviewer_agent_id="codex",
            review_package=ReviewPackage(
                task_id="TASK-099", title="T",
                acceptance_criteria=["AC-1"],
                changed_files=["f.py"], implementer_receipt="x",
            ),
        )
        d = req.to_dict()
        assert d["request_id"] == "rev-002"
        assert d["reviewer_agent_id"] == "codex"


class TestReviewResult:
    """Test review results."""

    def test_approved_result(self):
        result = ReviewResult(
            request_id="rev-001",
            verdict=ReviewVerdict.APPROVED,
            summary="All checks pass. Code is clean.",
            issues=[],
        )
        assert result.verdict == ReviewVerdict.APPROVED
        assert len(result.issues) == 0

    def test_revision_required_result(self):
        result = ReviewResult(
            request_id="rev-002",
            verdict=ReviewVerdict.REVISION_REQUIRED,
            summary="Missing error handling for edge cases.",
            issues=[
                {"severity": "high", "file": "src/auth.py", "description": "No timeout on API call"},
                {"severity": "medium", "file": "tests/test_auth.py", "description": "Missing integration test"},
            ],
        )
        assert result.verdict == ReviewVerdict.REVISION_REQUIRED
        assert len(result.issues) == 2

    def test_blocked_result(self):
        result = ReviewResult(
            request_id="rev-003",
            verdict=ReviewVerdict.BLOCKED,
            summary="Cannot proceed — depends on unresolved API design.",
        )
        assert result.verdict == ReviewVerdict.BLOCKED

    def test_escalate_result(self):
        result = ReviewResult(
            request_id="rev-004",
            verdict=ReviewVerdict.ESCALATE,
            summary="Architecture concern — needs senior review.",
            escalation_reason="Security implication in auth flow needs architect attention.",
        )
        assert result.verdict == ReviewVerdict.ESCALATE
        assert result.escalation_reason is not None

    def test_fix_task_created_for_revision(self):
        """Create a fix-task reference for RevisionRequired."""
        result = ReviewResult(
            request_id="rev-005",
            verdict=ReviewVerdict.REVISION_REQUIRED,
            summary="Fix needed",
            issues=[{"severity": "high", "description": "Bug"}],
            fix_task_id="TASK-015",
        )
        assert result.fix_task_id == "TASK-015"


class TestReviewManager:
    """Test the review manager."""

    @pytest.fixture
    def manager(self):
        return ReviewManager()

    @pytest.fixture
    def sample_package(self):
        return ReviewPackage(
            task_id="TASK-001", title="Test",
            acceptance_criteria=["AC-1"],
            changed_files=["f.py"], implementer_receipt="done",
        )

    def test_submit_review(self, manager, sample_package):
        req = manager.submit(
            task_id="TASK-001",
            reviewer_agent_id="codex-reviewer",
            review_package=sample_package,
        )
        assert req.status == "pending"

    def test_complete_review_approved(self, manager, sample_package):
        req = manager.submit(task_id="TASK-001", reviewer_agent_id="codex",
                            review_package=sample_package)
        result = manager.complete(
            request_id=req.request_id,
            verdict=ReviewVerdict.APPROVED,
            summary="LGTM",
        )
        assert result.verdict == ReviewVerdict.APPROVED
        # The request status should be updated.
        updated = manager.get_request(req.request_id)
        assert updated.status == "completed"

    def test_complete_nonexistent_request(self, manager):
        with pytest.raises(ReviewError):
            manager.complete(request_id="nonexistent", verdict=ReviewVerdict.APPROVED, summary="x")

    def test_list_pending_reviews(self, manager, sample_package):
        manager.submit(task_id="TASK-001", reviewer_agent_id="a", review_package=sample_package)
        pkg2 = ReviewPackage(
            task_id="TASK-002", title="T2",
            acceptance_criteria=["AC-1"],
            changed_files=["f.py"], implementer_receipt="done",
        )
        manager.submit(task_id="TASK-002", reviewer_agent_id="b", review_package=pkg2)
        pending = manager.list_pending()
        assert len(pending) == 2

    def test_completed_review_not_in_pending(self, manager, sample_package):
        req = manager.submit(task_id="TASK-001", reviewer_agent_id="a",
                            review_package=sample_package)
        manager.complete(req.request_id, ReviewVerdict.APPROVED, "done")
        pending = manager.list_pending()
        assert len(pending) == 0

    def test_get_reviews_by_task(self, manager, sample_package):
        manager.submit(task_id="TASK-001", reviewer_agent_id="a", review_package=sample_package)
        reviews = manager.get_by_task("TASK-001")
        assert len(reviews) == 1

    def test_duplicate_review_rejected(self, manager, sample_package):
        """Prevent multiple pending reviews for one task."""
        manager.submit(task_id="TASK-001", reviewer_agent_id="a", review_package=sample_package)
        with pytest.raises(ReviewError):
            manager.submit(task_id="TASK-001", reviewer_agent_id="b", review_package=sample_package)
