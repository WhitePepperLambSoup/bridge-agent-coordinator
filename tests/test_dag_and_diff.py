"""Unit tests for DAG Task Orchestration and Code Diff Inspection."""

import os
import subprocess
import pytest
from bridgelib.database import init_database
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.state_machine import TaskState


@pytest.fixture
def dag_env(tmp_path):
    repo_dir = str(tmp_path / "repo")
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)

    dummy_file = os.path.join(repo_dir, "base.txt")
    with open(dummy_file, "w") as f:
        f.write("initial\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    db_path = str(tmp_path / "bridge.db")
    db = init_database(db_path)
    coord = BridgeCoordinator(database=db)
    pid = coord.init_project("DAG Project", repo_dir)
    gid = coord.create_goal(pid, "Main Goal")

    t1 = coord.create_task(gid, "Root Task 1")
    t2 = coord.create_task(gid, "Branch Task 2")
    t3 = coord.create_task(gid, "Branch Task 3")
    t4 = coord.create_task(gid, "Join Task 4")

    return {
        "coord": coord,
        "pid": pid,
        "gid": gid,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "t4": t4,
        "repo_dir": repo_dir,
    }


def test_dependency_basic_crud_and_validation(dag_env):
    coord = dag_env["coord"]
    t1 = dag_env["t1"]
    t2 = dag_env["t2"]

    # 1. Add valid dependency: t2 depends on t1
    assert coord.add_task_dependency(t2, t1) is True
    assert coord.db.get_task_dependencies(t2) == [t1]
    assert coord.db.get_dependent_tasks(t1) == [t2]

    # 2. Reject self dependency
    with pytest.raises(CoordinatorError, match="cannot depend on itself"):
        coord.add_task_dependency(t1, t1)

    # 3. Reject nonexistent task
    with pytest.raises(CoordinatorError, match="not found"):
        coord.add_task_dependency(t1, "NONEXISTENT")

    # 4. Remove dependency
    assert coord.remove_task_dependency(t2, t1) is True
    assert coord.db.get_task_dependencies(t2) == []


def test_circular_dependency_detection(dag_env):
    coord = dag_env["coord"]
    t1 = dag_env["t1"]
    t2 = dag_env["t2"]
    t3 = dag_env["t3"]

    # Build chain: t2 -> t1 (t2 depends on t1), t3 -> t2 (t3 depends on t2)
    coord.add_task_dependency(t2, t1)
    coord.add_task_dependency(t3, t2)

    # Attempting to make t1 depend on t3 creates cycle (t1 -> t3 -> t2 -> t1)
    with pytest.raises(CoordinatorError, match="Circular dependency detected"):
        coord.add_task_dependency(t1, t3)


def test_dag_topological_layers_and_satisfaction(dag_env):
    coord = dag_env["coord"]
    pid = dag_env["pid"]
    t1 = dag_env["t1"]
    t2 = dag_env["t2"]
    t3 = dag_env["t3"]
    t4 = dag_env["t4"]

    # Build Diamond DAG:
    #       t1
    #      /  \
    #     t2  t3
    #      \  /
    #       t4
    coord.add_task_dependency(t2, t1)
    coord.add_task_dependency(t3, t1)
    coord.add_task_dependency(t4, t2)
    coord.add_task_dependency(t4, t3)

    # Initial plan
    layers = coord.get_dag_execution_plan(pid)
    assert len(layers) == 3
    assert layers[0] == [t1]
    assert sorted(layers[1]) == sorted([t2, t3])
    assert layers[2] == [t4]

    # Check dependencies met status
    met, unmet = coord.check_task_dependencies_met(t2)
    assert met is False
    assert unmet == [t1]

    # Directly set t1 to DONE
    coord.db.conn.execute("UPDATE tasks SET state = 'done' WHERE id = ?", (t1,))
    coord.db.conn.commit()

    met2, unmet2 = coord.check_task_dependencies_met(t2)
    assert met2 is True
    assert unmet2 == []

    # Re-calculate DAG: t1 is completed, so next layer is [t2, t3]
    layers_after = coord.get_dag_execution_plan(pid)
    assert len(layers_after) == 2
    assert sorted(layers_after[0]) == sorted([t2, t3])
    assert layers_after[1] == [t4]


def test_get_task_diff(dag_env):
    coord = dag_env["coord"]
    repo_dir = dag_env["repo_dir"]
    t1 = dag_env["t1"]

    # Modify a file in repo
    file_path = os.path.join(repo_dir, "base.txt")
    with open(file_path, "a") as f:
        f.write("added line\n")

    diff_data = coord.get_task_diff(t1)
    assert diff_data["task_id"] == t1
    assert "base.txt" in diff_data["files_changed"]
    assert "+added line" in diff_data["diff_text"]
