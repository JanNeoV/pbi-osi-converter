from __future__ import annotations

from .schemas import ChangeStatus


class InvalidStateTransition(ValueError):
    """Raised when a change lifecycle transition is not permitted."""


_ALLOWED_TRANSITIONS: dict[ChangeStatus, frozenset[ChangeStatus]] = {
    ChangeStatus.DRAFT: frozenset({
        ChangeStatus.PROPOSED,
        ChangeStatus.NO_OP,
        ChangeStatus.MANUAL_REVIEW_REQUIRED,
        ChangeStatus.FAILED,
    }),
    ChangeStatus.PROPOSED: frozenset({
        ChangeStatus.APPROVED,
        ChangeStatus.REJECTED,
        ChangeStatus.DISCARDED,
        ChangeStatus.FAILED,
    }),
    ChangeStatus.NO_OP: frozenset({ChangeStatus.DISCARDED, ChangeStatus.REJECTED}),
    ChangeStatus.MANUAL_REVIEW_REQUIRED: frozenset({ChangeStatus.DISCARDED, ChangeStatus.REJECTED}),
    ChangeStatus.APPROVED: frozenset({ChangeStatus.APPLIED_LOCAL, ChangeStatus.REJECTED, ChangeStatus.FAILED}),
    ChangeStatus.APPLIED_LOCAL: frozenset({ChangeStatus.VALIDATED, ChangeStatus.ROLLED_BACK, ChangeStatus.FAILED}),
    ChangeStatus.VALIDATED: frozenset({ChangeStatus.ROLLED_BACK}),
    ChangeStatus.REJECTED: frozenset(),
    ChangeStatus.FAILED: frozenset({ChangeStatus.ROLLED_BACK}),
    ChangeStatus.ROLLED_BACK: frozenset(),
    ChangeStatus.DISCARDED: frozenset(),
}


def allowed_transitions(status: ChangeStatus) -> frozenset[ChangeStatus]:
    return _ALLOWED_TRANSITIONS[status]


def validate_transition(current: ChangeStatus, proposed: ChangeStatus) -> None:
    if proposed not in allowed_transitions(current):
        raise InvalidStateTransition(f"Transition from {current.value} to {proposed.value} is not allowed.")
