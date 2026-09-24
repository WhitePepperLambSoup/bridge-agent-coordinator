import os
import tempfile
from datetime import datetime, timezone

import pytest

from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.database import init_database
from bridgelib.review import ReviewPackage, ReviewVerdict
from bridgelib.safety import ActionPolicy
from bridgelib.state_machine import TaskState


@pytest.fixture
def coordinator():
    with tempfile.TemporaryDirectory() as directory:
        database = init_database(os.path.join(directory, "bridge.db"))
        yield BridgeCoordinator(database), database
        database.close()


def test_project_safety_overrides_are_persistent_and_isolated(coordinator):
    coord, database = coordinator
    project_a = coord.init_project("A", tempfile.gettempdir())
    project_b = coord.init_project("B", tempfile.gettempdir())

    coord.set_safety_override(project_a, "create_worktree", "disabled")
    assert coord.get_safety_policy(project_a).is_disabled("create_worktree")
    assert not coord.get_safety_policy(project_b).is_disabled("create_worktree")

    restarted = BridgeCoordinator(database)
    assert restarted.get_safety_policy(project_a).is_disabled("create_worktree")
    assert not restarted.get_safety_policy(project_b).is_disabled("create_worktree")


def test_legacy_active_policy_override_is_persisted(coordinator):
    coord, database = coordinator
    project_id = coord.init_project("A", tempfile.gettempdir())
    coord.safety.set_override("create_worktree", ActionPolicy.DISABLED)

    restarted = BridgeCoordinator(database)
    assert restarted.get_safety_policy(project_id).is_disabled("create_worktree")


def test_validation_commands_are_persistent_and_project_scoped(coordinator):
    coord, database = coordinator
    project_a = coord.init_project("A", tempfile.gettempdir())
    project_b = coord.init_project("B", tempfile.gettempdir())
    coord.register_check_command(
        "custom-check", "echo", ["ok"], project_id=project_a, confirmed=True,
    )

    restarted = BridgeCoordinator(database)
    commands_a = restarted.db.list_validation_commands(project_a)
    commands_b = restarted.db.list_validation_commands(project_b)
    assert commands_a["custom-check"] == {
        "executable": "echo", "args": ["ok"],
    }
    assert "custom-check" not in commands_b


def test_approval_rejects_review_from_previous_attempt(coordinator):
    coord, _database = coordinator
    project_id = coord.init_project("A", tempfile.gettempdir())
    implementer = coord.add_agent(project_id, "I", roles=["implementer"])
    reviewer = coord.add_agent(
        project_id, "R", roles=["reviewer"], can_review=True,
    )
    goal_id = coord.create_goal(project_id, "G")
    task_id = coord.create_task(
        goal_id, "T", allowed_paths=["src/**"],
        acceptance_criteria=["AC"], required_checks=["unit-tests"],
    )
    coord.transition_task(task_id, TaskState.PLANNING, confirmed=True)
    coord.transition_task(task_id, TaskState.READY, confirmed=True)
    coord.assign_task(task_id, implementer, reviewer)
    lease = coord.acquire_lease(task_id, implementer, resource_path="src/**")
    first_attempt = coord.create_attempt(task_id, implementer, lease.lease_id)
    coord.transition_task(task_id, TaskState.IN_PROGRESS, confirmed=True)
    coord.transition_task(task_id, TaskState.SUBMITTED)
    review_id = coord.submit_review(
        task_id, reviewer, ReviewPackage(task_id=task_id, title="Review"),
    )
    coord.complete_review(
        review_id, ReviewVerdict.APPROVED, reviewer_agent_id=reviewer,
    )
    coord.complete_attempt(first_attempt)
    second_attempt = coord.create_attempt(task_id, implementer, lease.lease_id)
    coord.db.conn.execute(
        """INSERT INTO validations
           (id, task_id, attempt_id, check_id, status, exit_code, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            "validation-2", task_id, second_attempt, "unit-tests", "passed", 0,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    coord.db.conn.commit()
    coord.transition_task(task_id, TaskState.VALIDATING)

    with pytest.raises(CoordinatorError, match="approved review"):
        coord.transition_task(task_id, TaskState.APPROVED, confirmed=True)
