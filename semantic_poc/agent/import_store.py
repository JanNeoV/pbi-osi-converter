from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from semantic_poc.src.models import PROJECT_ROOT

from .import_models import (
    ImportAuthorityState,
    ImportDiscardState,
    ImportDiscardTombstone,
    ImportProposalBatch,
    ImportRun,
    canonical_json_text,
    validate_import_id,
)


DEFAULT_IMPORT_DIR = PROJECT_ROOT / "semantic_poc" / "imports"
RUN_RECORD_NAME = "run.json"
PROPOSAL_BATCH_RECORD_NAME = "proposals.json"
DISCARD_RECORD_NAME = "discard.json"
RUN_ARTIFACT_NAMES = frozenset({"inventory.json", "inventory.md", "report.md"})


class ImportStoreError(RuntimeError):
    """Base error for immutable Power BI import persistence."""


class ImportAlreadyExistsError(ImportStoreError):
    pass


class ImportNotFoundError(ImportStoreError):
    pass


class ImportProtectedError(ImportStoreError):
    pass


class ImportConflictError(ImportStoreError):
    pass


class ImportStore:
    """Append-only store for import runs, proposal batches, and discard tombstones."""

    def __init__(self, root: Path = DEFAULT_IMPORT_DIR) -> None:
        self.configured_root = Path(root).absolute()
        self.root = self.configured_root.resolve()

    def _assert_no_symlink_components(self, path: Path) -> None:
        candidate = Path(path.anchor)
        for component in path.parts[1:]:
            candidate = candidate / component
            if candidate.is_symlink():
                raise ImportStoreError(
                    f"Import store paths must not contain symbolic links: {candidate}"
                )
            if not candidate.exists():
                break

    def _ensure_root(self) -> None:
        self._assert_no_symlink_components(self.configured_root)
        try:
            self.configured_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ImportStoreError(f"Import store root could not be created: {exc}") from exc
        self._assert_no_symlink_components(self.configured_root)
        if self.configured_root.is_symlink() or self.configured_root.resolve() != self.root:
            raise ImportStoreError("Import store root must not be a symbolic link.")
        if not self.root.is_dir():
            raise ImportStoreError("Import store root must be a directory.")

    def _assert_existing_root(self) -> bool:
        self._assert_no_symlink_components(self.configured_root)
        if not self.configured_root.exists():
            return False
        if (
            self.configured_root.is_symlink()
            or self.configured_root.resolve() != self.root
            or not self.root.is_dir()
        ):
            raise ImportStoreError("Import store root must be a safe directory, not a symbolic link.")
        return True

    def run_path(self, import_id: str) -> Path:
        validate_import_id(import_id)
        path = self.root / import_id
        if path.parent != self.root:
            raise ValueError("Import path escapes the configured import store.")
        return path

    def _safe_existing_run_dir(self, import_id: str) -> Path:
        run_dir = self.run_path(import_id)
        if not run_dir.exists():
            raise ImportNotFoundError(f"Import run does not exist: {import_id}")
        if run_dir.is_symlink() or not run_dir.is_dir() or run_dir.resolve().parent != self.root:
            raise ImportStoreError(f"Import run directory is unsafe: {import_id}")
        return run_dir

    def _record_path(self, import_id: str, filename: str) -> Path:
        run_dir = self.run_path(import_id)
        if filename not in {
            RUN_RECORD_NAME,
            PROPOSAL_BATCH_RECORD_NAME,
            DISCARD_RECORD_NAME,
            *RUN_ARTIFACT_NAMES,
        }:
            raise ValueError(f"Unsupported import artifact name: {filename!r}")
        path = run_dir / filename
        if path.parent != run_dir:
            raise ValueError("Import record path escapes its run directory.")
        return path

    @contextmanager
    def _locked(self, import_id: str) -> Iterator[None]:
        validate_import_id(import_id)
        self._ensure_root()
        lock_path = self.root / f".{import_id}.lock"
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ImportProtectedError(
                f"Import run is locked by another operation: {import_id}"
            ) from exc
        except OSError as exc:
            raise ImportStoreError(f"Import lock could not be created: {import_id}: {exc}") from exc
        try:
            os.write(descriptor, f"semantic-agent import {import_id}\n".encode("utf-8"))
            os.fsync(descriptor)
            yield
        finally:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            # Windows does not consistently allow opening directories for fsync.
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _write_bytes(path: Path, payload: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    @classmethod
    def _write_json(cls, path: Path, data: Mapping[str, Any]) -> None:
        cls._write_bytes(path, canonical_json_text(data, pretty=True).encode("utf-8"))

    def _cleanup_stage(self, stage: Path) -> None:
        if stage.parent != self.root or not stage.name.startswith(".import-stage-"):
            raise ImportStoreError("Refusing to clean an unverified import staging directory.")
        if stage.is_symlink():
            raise ImportStoreError("Refusing to clean a symbolic-link import staging directory.")
        if not stage.exists():
            return
        for item in stage.iterdir():
            if item.is_symlink() or not item.is_file() or item.parent != stage:
                raise ImportStoreError("Import staging directory contains an unsafe entry.")
            item.unlink()
        stage.rmdir()

    @staticmethod
    def _normalize_artifacts(
        run: ImportRun,
        artifacts: Mapping[str, str | bytes] | None,
    ) -> dict[str, bytes]:
        result = {
            "inventory.json": canonical_json_text(run.inventory, pretty=True).encode("utf-8")
        }
        if artifacts is None:
            return result
        if not isinstance(artifacts, Mapping):
            raise ValueError("artifacts must be a mapping of safe artifact names to text or bytes.")
        for name, value in artifacts.items():
            if name not in RUN_ARTIFACT_NAMES:
                raise ValueError(f"Unsupported import artifact name: {name!r}")
            if not isinstance(value, (str, bytes)):
                raise ValueError(f"Import artifact {name!r} must be text or bytes.")
            payload = value.encode("utf-8") if isinstance(value, str) else value
            if name == "inventory.json":
                try:
                    supplied = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("inventory.json must contain valid UTF-8 JSON.") from exc
                if canonical_json_text(supplied) != canonical_json_text(run.inventory):
                    raise ValueError("inventory.json must describe the ImportRun inventory exactly.")
                payload = canonical_json_text(run.inventory, pretty=True).encode("utf-8")
            result[name] = payload
        return result

    def save_run(
        self,
        run: ImportRun,
        *,
        artifacts: Mapping[str, str | bytes] | None = None,
    ) -> Path:
        validated = ImportRun.from_dict(run.to_dict())
        artifact_payloads = self._normalize_artifacts(validated, artifacts)
        with self._locked(validated.import_id):
            final_dir = self.run_path(validated.import_id)
            if final_dir.exists() or final_dir.is_symlink():
                raise ImportAlreadyExistsError(
                    f"Import run already exists: {validated.import_id}"
                )
            stage = Path(tempfile.mkdtemp(prefix=".import-stage-", dir=self.root))
            try:
                self._write_json(stage / RUN_RECORD_NAME, validated.to_dict())
                for name in sorted(artifact_payloads):
                    self._write_bytes(stage / name, artifact_payloads[name])
                self._fsync_directory(stage)
                try:
                    os.rename(stage, final_dir)
                except OSError as exc:
                    if final_dir.exists() or final_dir.is_symlink():
                        raise ImportAlreadyExistsError(
                            f"Import run already exists: {validated.import_id}"
                        ) from exc
                    raise ImportStoreError(
                        f"Import run could not be published atomically: {validated.import_id}: {exc}"
                    ) from exc
                self._fsync_directory(self.root)
                return final_dir
            finally:
                if stage.exists():
                    self._cleanup_stage(stage)

    def _publish_record(
        self,
        import_id: str,
        filename: str,
        data: Mapping[str, Any],
    ) -> Path:
        run_dir = self._safe_existing_run_dir(import_id)
        path = self._record_path(import_id, filename)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=run_dir,
                prefix=f".{filename}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(canonical_json_text(data, pretty=True).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError as exc:
                raise ImportAlreadyExistsError(
                    f"Import record already exists: {import_id}/{filename}"
                ) from exc
            except OSError as exc:
                if path.exists() or path.is_symlink():
                    raise ImportAlreadyExistsError(
                        f"Import record already exists: {import_id}/{filename}"
                    ) from exc
                raise ImportStoreError(
                    f"Import record could not be published atomically: {import_id}/{filename}: {exc}"
                ) from exc
            self._fsync_directory(run_dir)
            return path
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _load_json_record(self, import_id: str, filename: str) -> Mapping[str, Any]:
        run_dir = self._safe_existing_run_dir(import_id)
        path = self._record_path(import_id, filename)
        if not path.exists():
            raise ImportNotFoundError(f"Import record does not exist: {import_id}/{filename}")
        if path.is_symlink() or not path.is_file() or path.resolve().parent != run_dir.resolve():
            raise ImportStoreError(f"Import record is unsafe: {import_id}/{filename}")
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ImportStoreError(f"Invalid stored import record {import_id}/{filename}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise ImportStoreError(f"Stored import record must be an object: {import_id}/{filename}")
        return data

    def load_run(self, import_id: str) -> ImportRun:
        try:
            return ImportRun.from_dict(self._load_json_record(import_id, RUN_RECORD_NAME))
        except ImportStoreError:
            raise
        except (TypeError, ValueError) as exc:
            raise ImportStoreError(f"Invalid stored import run {import_id}: {exc}") from exc

    def list_runs(self) -> tuple[ImportRun, ...]:
        if not self._assert_existing_root():
            return ()
        runs = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not path.name.startswith("imp_"):
                continue
            try:
                validate_import_id(path.name)
            except ValueError as exc:
                raise ImportStoreError(f"Invalid import directory name: {path.name}") from exc
            if path.is_symlink() or not path.is_dir() or path.resolve().parent != self.root:
                raise ImportStoreError(f"Import run directory is unsafe: {path.name}")
            runs.append(self.load_run(path.name))
        return tuple(sorted(runs, key=lambda item: (item.created_at, item.import_id)))

    def save_proposal_batch(self, batch: ImportProposalBatch) -> Path:
        validated = ImportProposalBatch.from_dict(batch.to_dict())
        with self._locked(validated.import_id):
            run = self.load_run(validated.import_id)
            if self.is_discarded(validated.import_id):
                raise ImportProtectedError(
                    f"Discarded import cannot receive proposals: {validated.import_id}"
                )
            if validated.source_snapshot_hash != run.source_snapshot_hash:
                raise ImportConflictError("Proposal batch source snapshot does not match its import run.")
            if validated.import_semantic_content_hash != run.semantic_content_hash:
                raise ImportConflictError("Proposal batch semantic hash does not match its import run.")
            if validated.extraction_version != run.extraction_version:
                raise ImportConflictError("Proposal batch extraction version does not match its import run.")
            if validated.source_model_id != run.source_model_id:
                raise ImportConflictError("Proposal batch source model ID does not match its import run.")
            if validated.source_model_path != run.source_model_path:
                raise ImportConflictError("Proposal batch source model path does not match its import run.")
            if any(_proposal_is_protected(item) for item in validated.proposals):
                raise ImportProtectedError(
                    "A new import proposal batch cannot contain approved or applied proposals."
                )
            return self._publish_record(
                validated.import_id,
                PROPOSAL_BATCH_RECORD_NAME,
                validated.to_dict(),
            )

    # Concise aliases keep CLI integration readable.
    save_batch = save_proposal_batch

    def load_proposal_batch(self, import_id: str) -> ImportProposalBatch:
        try:
            return ImportProposalBatch.from_dict(
                self._load_json_record(import_id, PROPOSAL_BATCH_RECORD_NAME)
            )
        except ImportStoreError:
            raise
        except (TypeError, ValueError) as exc:
            raise ImportStoreError(f"Invalid stored proposal batch {import_id}: {exc}") from exc

    load_batch = load_proposal_batch

    def try_load_proposal_batch(self, import_id: str) -> ImportProposalBatch | None:
        try:
            return self.load_proposal_batch(import_id)
        except ImportNotFoundError:
            return None

    def load_discard(self, import_id: str) -> ImportDiscardTombstone:
        try:
            return ImportDiscardTombstone.from_dict(
                self._load_json_record(import_id, DISCARD_RECORD_NAME)
            )
        except ImportStoreError:
            raise
        except (TypeError, ValueError) as exc:
            raise ImportStoreError(f"Invalid stored discard tombstone {import_id}: {exc}") from exc

    def try_load_discard(self, import_id: str) -> ImportDiscardTombstone | None:
        try:
            return self.load_discard(import_id)
        except ImportNotFoundError:
            return None

    def is_discarded(self, import_id: str) -> bool:
        return self.try_load_discard(import_id) is not None

    def assert_discardable(
        self,
        import_id: str,
        *,
        proposal_states: Sequence[Any] = (),
    ) -> None:
        self.load_run(import_id)
        batch = self.try_load_proposal_batch(import_id)
        candidates: list[Any] = list(batch.proposals if batch is not None else ())
        candidates.extend(proposal_states)
        for value in candidates:
            if not isinstance(value, Mapping) and hasattr(value, "to_dict"):
                value = value.to_dict()
            if not isinstance(value, Mapping):
                raise ValueError("proposal_states must contain JSON objects or records exposing to_dict().")
            if _proposal_is_protected(value):
                change_id = value.get("change_id", "unknown")
                raise ImportProtectedError(
                    f"Import cannot be discarded after a child is approved or applied: {change_id}"
                )

    def discard_run(
        self,
        import_id: str,
        *,
        actor: str = "local-user",
        reason: str = "Discarded by local user.",
        proposal_states: Sequence[Any] = (),
        now=None,
    ) -> ImportDiscardTombstone:
        validate_import_id(import_id)
        with self._locked(import_id):
            run = self.load_run(import_id)
            if self.is_discarded(import_id):
                raise ImportAlreadyExistsError(f"Import run is already discarded: {import_id}")
            self.assert_discardable(import_id, proposal_states=proposal_states)
            batch = self.try_load_proposal_batch(import_id)
            authority_state = (
                batch.authority_state
                if batch is not None
                else ImportAuthorityState.POWER_BI_DISCOVERED
            )
            tombstone = ImportDiscardTombstone.create(
                import_id=run.import_id,
                authority_state=authority_state,
                actor=actor,
                reason=reason,
                now=now,
            )
            self._publish_record(import_id, DISCARD_RECORD_NAME, tombstone.to_dict())
            return tombstone

    def effective_discard_state(self, import_id: str) -> ImportDiscardState:
        self.load_run(import_id)
        return (
            ImportDiscardState.DISCARDED
            if self.is_discarded(import_id)
            else ImportDiscardState.ACTIVE
        )

    def load_artifact(self, import_id: str, name: str) -> bytes:
        if name not in RUN_ARTIFACT_NAMES:
            raise ValueError(f"Unsupported import artifact name: {name!r}")
        run_dir = self._safe_existing_run_dir(import_id)
        path = self._record_path(import_id, name)
        if not path.exists():
            raise ImportNotFoundError(f"Import artifact does not exist: {import_id}/{name}")
        if path.is_symlink() or not path.is_file() or path.resolve().parent != run_dir.resolve():
            raise ImportStoreError(f"Import artifact is unsafe: {import_id}/{name}")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ImportStoreError(f"Import artifact could not be read: {import_id}/{name}: {exc}") from exc

    def load_text_artifact(self, import_id: str, name: str) -> str:
        try:
            return self.load_artifact(import_id, name).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImportStoreError(f"Import artifact is not UTF-8 text: {import_id}/{name}") from exc


def _proposal_is_protected(proposal: Mapping[str, Any]) -> bool:
    audit_events = proposal.get("audit_events") or ()
    if isinstance(audit_events, Sequence) and not isinstance(
        audit_events, (str, bytes, bytearray)
    ):
        for event in audit_events:
            if not isinstance(event, Mapping):
                continue
            if event.get("result", "SUCCESS") == "SUCCESS" and (
                event.get("action") == "APPROVE"
                or event.get("to_status") in {"APPROVED", "APPLIED_LOCAL", "VALIDATED"}
            ):
                return True
    if proposal.get("approval_state") == "APPROVED":
        return True
    if proposal.get("status") in {"APPROVED", "APPLIED_LOCAL", "VALIDATED"}:
        return True
    local_state = proposal.get("local_application_state")
    if local_state not in {None, "NOT_REQUESTED"}:
        return True
    deployment_state = proposal.get("deployment_state")
    return deployment_state not in {None, "NOT_REQUESTED", "NOT_PERFORMED"}
