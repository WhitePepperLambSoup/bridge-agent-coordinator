"""Bridge receipt importer with stability checks, hash deduplication, and startup rescans.

Design reference: docs/bridge-design/05-agent-file-protocol.md, import algorithm
"""

import os
import hashlib
import time
import threading
import logging
from datetime import datetime, timezone

from bridgelib.protocol import parse_receipt, validate_receipt, ProtocolError

logger = logging.getLogger(__name__)

# Read a file only after its size and modification time remain unchanged for 750 ms
STABILITY_WINDOW_MS = 750
MAX_RECEIPT_SIZE_BYTES = 1_048_576  # 1 MB


class ReceiptImportError(Exception):
    pass


class ReceiptImporter:
    """Import stable receipt files while deduplicating them by content hash."""

    def __init__(self, db, stability_ms: int = STABILITY_WINDOW_MS):
        self.db = db
        self.stability_ms = stability_ms
        self._imported_hashes: set[str] = set()
        self._lock = threading.Lock()

    def scan_and_import(self, task_dir: str, task_id: str, attempt: int,
                        lease_id: str, agent_id: str,
                        base_commit: str = "", branch: str = "",
                        allowed_paths: list | None = None,
                        forbidden_paths: list | None = None) -> dict | None:
        """Scan a task directory and import its receipt once stable.

        Return the import result or None.

        P0 fix:
        - Verify that lease_id exists in the database and is active.
        - Verify that agent_id owns the task.
        - Verify that submission_commit exists in the Git repository when available.
        - Validate receipt content before database cross-checks so mismatch errors come first.
        """
        receipt_path = os.path.join(task_dir, "RECEIPT.md")
        if not os.path.isfile(receipt_path):
            return None

        # Wait for the stability window
        if not self._wait_stable(receipt_path):
            raise ReceiptImportError(
                f"Receipt file {receipt_path} did not stabilize within timeout"
            )

        # Read the file while enforcing the size limit
        file_size = os.path.getsize(receipt_path)
        if file_size > MAX_RECEIPT_SIZE_BYTES:
            raise ReceiptImportError(
                f"Receipt file too large: {file_size} bytes (max {MAX_RECEIPT_SIZE_BYTES})"
            )

        with open(receipt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Deduplicate by content hash
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            if content_hash in self._imported_hashes:
                logger.info(f"Duplicate receipt ignored: {content_hash[:16]}")
                return None

            # Check for a duplicate in the database
            existing = self.db.conn.execute(
                "SELECT id FROM receipts WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if existing:
                self._imported_hashes.add(content_hash)
                logger.info(f"Receipt already in database: {content_hash[:16]}")
                return None

        # Parse the receipt
        try:
            receipt = parse_receipt(content)
        except ProtocolError as e:
            raise ReceiptImportError(f"Failed to parse receipt: {e}")

        # First cross-check receipt content against the caller-provided values
        errors = validate_receipt(
            receipt,
            expected_task_id=task_id,
            expected_attempt=attempt,
            expected_lease_id=lease_id,
            expected_agent_id=agent_id,
        )
        if errors:
            raise ReceiptImportError(
                f"Receipt validation failed: {'; '.join(errors)}"
            )

        # ── Database cross-checks after receipt content validation ──
        # 1. Verify that lease_id exists in the database and is active
        lease = self.db.get_lease(lease_id)
        if lease is None:
            raise ReceiptImportError(
                f"Lease '{lease_id}' does not exist in database — "
                f"cannot import receipt with unknown lease"
            )
        if lease.get("status") != "active":
            raise ReceiptImportError(
                f"Lease '{lease_id}' is not active (status: {lease.get('status')}) — "
                f"cannot import receipt with inactive lease"
            )
        # Verify that the lease has not expired
        expires_at_str = lease.get("expires_at", "")
        if expires_at_str:
            try:
                from datetime import datetime as _dt
                expires_at = _dt.fromisoformat(expires_at_str)
                if _dt.now(expires_at.tzinfo) > expires_at:
                    raise ReceiptImportError(
                        f"Lease '{lease_id}' has expired (expires_at: {expires_at_str}) — "
                        f"cannot import receipt with expired lease"
                    )
            except ReceiptImportError:
                raise
            except (ValueError, TypeError):
                pass  # Skip an expiration time that cannot be parsed

        # Verify that the lease belongs to the same task
        if lease.get("task_id") != task_id:
            raise ReceiptImportError(
                f"Lease '{lease_id}' belongs to task '{lease.get('task_id')}', "
                f"not '{task_id}'"
            )

        # 2. Verify that agent_id owns the task
        task = self.db.get_task(task_id)
        if task is None:
            raise ReceiptImportError(
                f"Task '{task_id}' does not exist in database"
            )
        if task.get("owner_agent_id") != agent_id:
            raise ReceiptImportError(
                f"Agent '{agent_id}' is not the owner of task '{task_id}' "
                f"(owner: {task.get('owner_agent_id')})"
            )

        # 3. Verify that the lease and receipt have the same agent_id
        if lease.get("agent_id") != agent_id:
            raise ReceiptImportError(
                f"Lease '{lease_id}' belongs to agent '{lease.get('agent_id')}', "
                f"not '{agent_id}'"
            )

        # 4. Verify that the receipt attempt matches the current database attempt
        current_attempt_row = self.db.conn.execute(
            "SELECT attempt_number FROM attempts WHERE task_id = ? "
            "AND status = 'in_progress' ORDER BY attempt_number DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if current_attempt_row:
            db_attempt_number = current_attempt_row["attempt_number"]
            if attempt != db_attempt_number:
                raise ReceiptImportError(
                    f"Receipt attempt ({attempt}) does not match current "
                    f"database attempt ({db_attempt_number}) for task '{task_id}'"
                )

        # ── Verify that submission_commit exists in Git when available ──
        if receipt.status == "completed" and receipt.submission_commit:
            project_id = task.get("project_id", "")
            if project_id:
                self._verify_commit_in_project(project_id, receipt.submission_commit)

        # Persist to the database
        receipt_id = f"rcpt-{content_hash[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        # Get the current attempt_id when available
        attempt_row = self.db.conn.execute(
            "SELECT id FROM attempts WHERE task_id = ? AND status = 'in_progress' "
            "ORDER BY attempt_number DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        attempt_id_db = attempt_row["id"] if attempt_row else None
        inserted = self.db.insert_receipt_if_absent(
            receipt_id=receipt_id,
            task_id=task_id,
            attempt_id=attempt_id_db,
            agent_id=agent_id,
            receipt_path=receipt_path,
            content_hash=content_hash,
            status="imported",
            import_result=f"status={receipt.status}",
            created_at=now,
        )
        if not inserted:
            with self._lock:
                self._imported_hashes.add(content_hash)
            logger.info(f"Receipt already imported: {content_hash[:16]}")
            return None

        with self._lock:
            self._imported_hashes.add(content_hash)

        logger.info(f"Receipt imported: {receipt_id} status={receipt.status}")
        return {
            "receipt_id": receipt_id,
            "content_hash": content_hash,
            "status": receipt.status,
            "submission_commit": receipt.submission_commit,
        }

    def _verify_commit_in_project(self, project_id: str, commit: str):
        """Verify that a commit exists in the project's Git repository.

        P0 fix: fail closed. Reject the receipt if the project path exists but
        is not a Git repository, or if Git verification fails. A receipt from
        a low-cost model is not trusted evidence.
        """
        try:
            proj = self.db.get_project(project_id)
            if not proj:
                raise ReceiptImportError(
                    f"Project '{project_id}' not found in database — "
                    f"cannot verify commit '{commit}'"
                )
            root_path = proj.get("root_path", "")
            if not root_path:
                raise ReceiptImportError(
                    f"Project '{project_id}' has no root_path — "
                    f"cannot verify commit '{commit}'"
                )
            if not os.path.isdir(root_path):
                raise ReceiptImportError(
                    f"Project root '{root_path}' does not exist — "
                    f"cannot verify commit '{commit}'"
                )
            from bridgelib.git_adapter import GitRepositoryAdapter, GitAdapterError
            adapter = GitRepositoryAdapter(root_path)
            status = adapter.check_repo()
            if not status.is_repo:
                raise ReceiptImportError(
                    f"Project root '{root_path}' is not a Git repository — "
                    f"cannot verify submission commit '{commit}'. "
                    f"Receipts with commits from non-Git projects are not accepted."
                )
            if not adapter.verify_commit_strict(commit):
                raise ReceiptImportError(
                    f"Submission commit '{commit}' does not exist in the project "
                    f"repository or is not a commit object"
                )
        except ReceiptImportError:
            raise
        except GitAdapterError as e:
            raise ReceiptImportError(
                f"Git verification failed for commit '{commit}': {e}"
            )
        except Exception as e:
            raise ReceiptImportError(
                f"Commit verification error for '{commit}': {e}"
            )

    def rescan_on_startup(self, task_dirs: list[str],
                          task_map: dict | None = None) -> list[dict]:
        """Rescan for unimported receipts at startup and import them.

        task_map: {directory: {"task_id":..., "attempt":..., "lease_id":..., "agent_id":...}}
        When provided, run scan_and_import for unimported receipts.
        """
        results = []
        for d in task_dirs:
            receipt_path = os.path.join(d, "RECEIPT.md")
            if not os.path.isfile(receipt_path):
                continue
            try:
                with open(receipt_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                existing = self.db.conn.execute(
                    "SELECT id FROM receipts WHERE content_hash = ?", (content_hash,)
                ).fetchone()
                if not existing:
                    # Attempt an actual import when a task mapping is available
                    if task_map and d in task_map:
                        info = task_map[d]
                        try:
                            imported = self.scan_and_import(
                                d, info["task_id"], info.get("attempt", 1),
                                info.get("lease_id", ""), info.get("agent_id", ""),
                            )
                            if imported:
                                results.append({
                                    "directory": d,
                                    "content_hash": content_hash[:16],
                                    "status": "imported",
                                    "receipt_id": imported.get("receipt_id", ""),
                                })
                                continue
                        except ReceiptImportError as e:
                            results.append({
                                "directory": d,
                                "content_hash": content_hash[:16],
                                "status": "import_failed",
                                "error": str(e),
                            })
                            continue
                    results.append({
                        "directory": d,
                        "content_hash": content_hash[:16],
                        "status": "unimported",
                    })
            except Exception as e:
                results.append({
                    "directory": d,
                    "error": str(e),
                })
        return results

    def _wait_stable(self, filepath: str, timeout_seconds: float = 5.0) -> bool:
        """Wait until file size and modification time remain stable for the window."""
        deadline = time.monotonic() + timeout_seconds
        last_size = -1
        last_mtime = -1.0
        stable_since = 0.0

        while time.monotonic() < deadline:
            try:
                stat = os.stat(filepath)
                current_size = stat.st_size
                current_mtime = stat.st_mtime
            except OSError:
                time.sleep(0.1)
                continue

            if current_size == last_size and current_mtime == last_mtime:
                if stable_since == 0.0:
                    stable_since = time.monotonic()
                elif (time.monotonic() - stable_since) * 1000 >= self.stability_ms:
                    return True
            else:
                stable_since = 0.0
                last_size = current_size
                last_mtime = current_mtime

            time.sleep(0.05)

        return False
