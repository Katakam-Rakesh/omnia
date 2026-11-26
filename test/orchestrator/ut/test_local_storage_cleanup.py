# Copyright 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Local storage teardown contracts (ER-ORCH-011 Story 6, TC-UT-009).

Covers:
- FR-5.1: Ordering guard refuses Orchestrator cleanup when Kafka PVCs still exist.
- FR-5.1: Preserve flag (preserve-sink-volumes: true) skips destructive disk removal.
- FR-5.1: Full teardown removes StorageClass, node labels, TopoLVM, and Volume Groups.
- FR-5.2: Export-safe local path skips PowerScale/Isilon REST API export deletions.
"""

# pylint: disable=redefined-outer-name,missing-function-docstring

from __future__ import annotations

from pathlib import Path
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
ORCH_ROLES = REPO_ROOT / "src" / "orchestrator" / "roles"
TELEMETRY_ROLES = REPO_ROOT / "src" / "telemetry" / "roles"
K8S_CLEANUP_TASKS = ORCH_ROLES / "cleanup" / "components" / "k8s" / "tasks"
TELEMETRY_CLEANUP_KAFKA = TELEMETRY_ROLES / "cleanup" / "tasks" / "kafka.yml"

GUARD_BLOCK_NAME = "Kafka Local Storage | Ordering safety guard (FR-5.1)"


@pytest.fixture
def pre_cleanup_tasks():
    """Load K8s pre_cleanup.yml tasks."""
    path = K8S_CLEANUP_TASKS / "pre_cleanup.yml"
    assert path.exists(), f"pre_cleanup.yml not found at {path}"
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture
def cleanup_local_storage_tasks():
    """Load cleanup_local_storage.yml tasks."""
    path = K8S_CLEANUP_TASKS / "cleanup_local_storage.yml"
    assert path.exists(), f"cleanup_local_storage.yml not found at {path}"
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture
def telemetry_kafka_cleanup_tasks():
    """Load telemetry cleanup kafka.yml tasks."""
    assert TELEMETRY_CLEANUP_KAFKA.exists()
    with open(TELEMETRY_CLEANUP_KAFKA, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_ordering_guard_queries_kafka_local_pvcs(cleanup_local_storage_tasks):
    """TC-UT-009: Ordering guard queries PVCs for omnia-kafka-local storageClass."""
    guard_block = next(
        (task for task in cleanup_local_storage_tasks if task.get("name") == GUARD_BLOCK_NAME),
        None,
    )
    assert guard_block is not None
    block_tasks = guard_block.get("block", [])
    query_task = next(
        (t for t in block_tasks if "Query active Kafka PVCs" in t.get("name", "")),
        None,
    )
    assert query_task is not None
    assert "omnia-kafka-local" in query_task.get("ansible.builtin.command", "")


def test_ordering_guard_fails_when_pvcs_present(cleanup_local_storage_tasks):
    """TC-UT-009: Ordering guard fails with actionable message when PVCs are active."""
    guard_block = next(
        (task for task in cleanup_local_storage_tasks if task.get("name") == GUARD_BLOCK_NAME),
        None,
    )
    assert guard_block is not None
    fail_task = next(
        (
            t
            for t in guard_block.get("block", [])
            if "Refuse cleanup when Kafka PVCs still exist" in t.get("name", "")
        ),
        None,
    )
    assert fail_task is not None
    fail_msg = fail_task.get("ansible.builtin.fail", {}).get("msg", "")
    assert "k8s_cleanup_kafka_active_pvc_fail_msg" in fail_msg


def test_cleanup_local_storage_honors_preserve_flag(
    cleanup_local_storage_tasks,
):
    """TC-UT-009: Local storage teardown is conditional on preserve flag being false."""
    process_block = next(
        (
            t
            for t in cleanup_local_storage_tasks
            if t.get("name")
            == "Kafka Local Storage | Process local storage teardown"
        ),
        None,
    )
    assert process_block is not None
    teardown_block = next(
        (
            t
            for t in process_block.get("block", [])
            if t.get("name") == "Kafka Local Storage | Execute teardown tasks"
        ),
        None,
    )
    assert teardown_block is not None
    when_conditions = teardown_block.get("when", [])
    assert any(
        "not (_local_storage_preserve | bool)" in cond
        for cond in when_conditions
    )


def test_cleanup_local_storage_deletes_storageclass_and_label(
    cleanup_local_storage_tasks,
):
    """TC-UT-009: Teardown removes omnia-kafka-local storageClass and node labels."""
    process_block = next(
        (
            t
            for t in cleanup_local_storage_tasks
            if t.get("name")
            == "Kafka Local Storage | Process local storage teardown"
        ),
        None,
    )
    assert process_block is not None
    teardown_tasks = next(
        (
            t
            for t in process_block.get("block", [])
            if t.get("name") == "Kafka Local Storage | Execute teardown tasks"
        ),
        None,
    ).get("block", [])

    task_names = [t.get("name", "") for t in teardown_tasks]
    assert any("Delete StorageClass" in name for name in task_names)
    assert any("Remove node label" in name for name in task_names)
    assert any("Uninstall TopoLVM release" in name for name in task_names)


def test_telemetry_cleanup_skips_isilon_exports_for_local_storage(
    telemetry_kafka_cleanup_tasks,
):
    """TC-UT-009 / FR-5.2: Telemetry cleanup bypasses remote Isilon API on local storage."""
    detect_task = next(
        (
            t
            for t in telemetry_kafka_cleanup_tasks
            if t.get("name") == "Kafka | Set local storage flag for Kafka PVs"
        ),
        None,
    )
    assert detect_task is not None
    set_fact = detect_task.get("ansible.builtin.set_fact", {})
    assert "omnia-kafka-local" in set_fact.get("_kafka_on_local_storage", "")

    delete_export_task = next(
        (
            t
            for t in telemetry_kafka_cleanup_tasks
            if t.get("name") == "Kafka | Delete Isilon NFS exports via API"
        ),
        None,
    )
    assert delete_export_task is not None
    when_conds = delete_export_task.get("when", [])
    assert any(
        "not (_kafka_on_local_storage | bool)" in cond for cond in when_conds
    )
