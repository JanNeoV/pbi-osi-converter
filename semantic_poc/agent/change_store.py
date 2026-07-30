from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator

from semantic_poc.src.models import PROJECT_ROOT

from .proposal_models import LocalApplicationState, PROPOSAL_SCHEMA_VERSION, ProposalRecord, ProposalStatus
from .schemas import MetricChangeRequest, utc_timestamp, validate_change_id


DEFAULT_CHANGE_DIR = PROJECT_ROOT / "semantic_poc" / "changes"


class ChangeStoreError(RuntimeError):
    """Base error for local change request persistence."""


class ChangeAlreadyExistsError(ChangeStoreError):
    pass


class ChangeNotFoundError(ChangeStoreError):
    pass


class ChangeProtectedError(ChangeStoreError):
    pass


class ChangeStore:
    def __init__(self, root: Path = DEFAULT_CHANGE_DIR) -> None:
        self.configured_root = root.absolute()
        self.root = root.resolve()

    def path_for(self, change_id: str) -> Path:
        validate_change_id(change_id)
        path = self.root / f"{change_id}.json"
        if path.parent.resolve() != self.root:
            raise ValueError("Change path escapes the configured change store.")
        return path

    def _publish(self, change_id: str, data: dict) -> Path:
        path = self.path_for(change_id)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.configured_root.is_symlink():
            raise ChangeStoreError("Change store root must not be a symbolic link.")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.root,
                prefix=".change-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError as exc:
                raise ChangeAlreadyExistsError(f"Change request already exists: {change_id}") from exc
            except OSError as exc:
                raise ChangeStoreError(
                    f"Change request could not be published atomically: {change_id}: {exc}"
                ) from exc
            return path
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @contextmanager
    def _locked(self, change_id: str) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / f".{change_id}.lock"
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ChangeProtectedError(f"Proposal is locked by another operation: {change_id}") from exc
        try:
            os.write(descriptor, f"semantic-agent {utc_timestamp()}\n".encode("utf-8"))
            os.fsync(descriptor)
            yield
        finally:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _replace(self, proposal: ProposalRecord) -> Path:
        validated = ProposalRecord.from_dict(proposal.to_dict())
        path = self.path_for(validated.change_id)
        if not path.is_file() or path.is_symlink() or path.resolve().parent != self.root:
            raise ChangeNotFoundError(f"Proposal does not exist safely: {validated.change_id}")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.root,
                prefix=".change-update-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(validated.to_dict(), handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            return path
        except OSError as exc:
            raise ChangeStoreError(f"Proposal could not be updated atomically: {validated.change_id}: {exc}") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def update_proposal(
        self,
        change_id: str,
        transform: Callable[[ProposalRecord], ProposalRecord],
    ) -> ProposalRecord:
        validate_change_id(change_id)
        with self._locked(change_id):
            current = self.load_proposal(change_id)
            updated = transform(current)
            if updated.change_id != current.change_id or updated.created_at != current.created_at:
                raise ChangeStoreError("Proposal updates must preserve identity.")
            if updated.schema_version != PROPOSAL_SCHEMA_VERSION:
                updated = replace(updated, schema_version=PROPOSAL_SCHEMA_VERSION)
            self._replace(updated)
            return updated

    def save(self, request: MetricChangeRequest) -> Path:
        validated = MetricChangeRequest.from_dict(request.to_dict())
        return self._publish(validated.change_id, validated.to_dict())

    def save_proposal(self, proposal: ProposalRecord) -> Path:
        validated = ProposalRecord.from_dict(proposal.to_dict())
        return self._publish(validated.change_id, validated.to_dict())

    def load(self, change_id: str) -> MetricChangeRequest:
        path = self.path_for(change_id)
        if not path.is_file():
            raise ChangeNotFoundError(f"Change request does not exist: {change_id}")
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return MetricChangeRequest.from_dict(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ChangeStoreError(f"Invalid stored change request {change_id}: {exc}") from exc

    def list(self) -> tuple[MetricChangeRequest, ...]:
        if not self.root.is_dir():
            return ()
        requests = []
        for path in sorted(self.root.glob("chg_*.json")):
            requests.append(self.load(path.stem))
        return tuple(requests)

    def load_proposal(self, change_id: str) -> ProposalRecord:
        path = self.path_for(change_id)
        if not path.is_file() or path.is_symlink():
            raise ChangeNotFoundError(f"Proposal does not exist: {change_id}")
        try:
            with path.open("r", encoding="utf-8") as handle:
                return ProposalRecord.from_dict(json.load(handle))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ChangeStoreError(f"Invalid stored proposal {change_id}: {exc}") from exc

    def list_proposals(self) -> tuple[ProposalRecord, ...]:
        if not self.root.is_dir():
            return ()
        proposals = []
        for path in self.root.glob("chg_*.json"):
            if path.is_symlink():
                raise ChangeStoreError(f"Symbolic-link proposal records are not allowed: {path.name}")
            proposals.append(self.load_proposal(path.stem))
        return tuple(sorted(proposals, key=lambda item: (item.created_at, item.change_id)))

    def discard_proposal(self, change_id: str) -> ProposalRecord:
        proposal = self.load_proposal(change_id)
        if proposal.local_application_state is not LocalApplicationState.NOT_REQUESTED:
            raise ChangeProtectedError(f"Proposal is protected by local application state: {change_id}")
        if proposal.approval_state.value not in {"NOT_REQUESTED", "PENDING", "REJECTED"}:
            raise ChangeProtectedError(f"Proposal is protected by approval state: {change_id}")
        if proposal.deployment_state.value != "NOT_REQUESTED":
            raise ChangeProtectedError(f"Proposal is protected by deployment state: {change_id}")
        if proposal.status not in {
            ProposalStatus.PROPOSED,
            ProposalStatus.NO_OP,
            ProposalStatus.MANUAL_REVIEW_REQUIRED,
        }:
            raise ChangeProtectedError(f"Proposal cannot be discarded from status {proposal.status.value}: {change_id}")

        def transition(current: ProposalRecord) -> ProposalRecord:
            if current.local_application_state is not LocalApplicationState.NOT_REQUESTED:
                raise ChangeProtectedError(f"Proposal is protected by local application state: {change_id}")
            if current.approval_state.value not in {"NOT_REQUESTED", "PENDING", "REJECTED"}:
                raise ChangeProtectedError(f"Proposal is protected by approval state: {change_id}")
            if current.status not in {
                ProposalStatus.PROPOSED,
                ProposalStatus.NO_OP,
                ProposalStatus.MANUAL_REVIEW_REQUIRED,
            }:
                raise ChangeProtectedError(
                    f"Proposal cannot be discarded from status {current.status.value}: {change_id}"
                )
            event = {
                "sequence": len(current.audit_events) + 1,
                "timestamp": utc_timestamp(),
                "actor": "local-user",
                "action": "DISCARD",
                "from_status": current.status.value,
                "to_status": ProposalStatus.DISCARDED.value,
                "result": "SUCCESS",
            }
            return replace(
                current,
                status=ProposalStatus.DISCARDED,
                canonical_application_available=False,
                audit_events=current.audit_events + (event,),
            )

        return self.update_proposal(change_id, transition)
