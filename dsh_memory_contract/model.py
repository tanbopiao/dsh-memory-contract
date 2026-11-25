"""dsh_memory_contract — data model for the memory seam contract suite.

Implements the Fact record and the in-force-only replacement rule from the
RFC draft in deepseek-ai/deepseek-harness Discussion #13749.

Pure standard library, Python 3.10+.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

STALENESS_CLASSES = ("probe", "provenance", "renewal")
STATES = ("in_force", "replaced", "retired")


class ContractError(Exception):
    """Base error for contract violations."""


class UnresolvableSourcePointer(ContractError):
    """A write carried a source_pointer that does not resolve in the event log."""


class ConflictStateError(ContractError):
    """A replace/retire was attempted on an entry that is not in_force."""


class DuplicateFactId(ContractError):
    """A second write used an existing fact_id (deterministic error, documented)."""


def _now() -> float:
    return time.time()


def _token_estimate(text: str) -> int:
    """Deterministic token estimate: ~4 chars per token, minimum 1."""
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class SourcePointer:
    """Points at the event that produced a fact: (session_id, event_offset)."""

    session_id: str
    event_offset: int

    def as_tuple(self) -> tuple[str, int]:
        return (self.session_id, self.event_offset)

    def to_json(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "event_offset": self.event_offset}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SourcePointer":
        return cls(session_id=data["session_id"], event_offset=int(data["event_offset"]))


@dataclass
class Revision:
    """An additive correction record: never overwrites, always dated and reasoned."""

    action: str  # "replaced" or "retired"
    actor_id: str
    reason: str
    recorded_at: float
    superseded_by: str | None = None  # fact_id of the replacement, for "replaced"


@dataclass
class Fact:
    """A single authoritative fact with provenance and lifecycle state."""

    statement: str
    source_pointer: SourcePointer
    staleness_class: str = "provenance"
    fact_id: str | None = None
    recorded_at: float = field(default_factory=_now)
    state: str = "in_force"
    superseded_by: str | None = None
    revisions: list[Revision] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.staleness_class not in STALENESS_CLASSES:
            raise ContractError(
                f"staleness_class must be one of {STALENESS_CLASSES}, got {self.staleness_class!r}"
            )
        if self.state not in STATES:
            raise ContractError(f"state must be one of {STATES}, got {self.state!r}")
        if self.fact_id is None:
            self.fact_id = self._derive_id()

    def _derive_id(self) -> str:
        digest = hashlib.sha256(
            f"{self.statement}|{self.source_pointer.as_tuple()}|{self.recorded_at}".encode()
        ).hexdigest()[:16]
        return f"f_{digest}"

    @property
    def in_force(self) -> bool:
        return self.state == "in_force"

    def token_cost(self) -> int:
        return _token_estimate(self.statement)

    def to_json(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "statement": self.statement,
            "source_pointer": self.source_pointer.to_json(),
            "recorded_at": self.recorded_at,
            "staleness_class": self.staleness_class,
            "state": self.state,
            "superseded_by": self.superseded_by,
            "revisions": [
                {
                    "action": r.action,
                    "actor_id": r.actor_id,
                    "reason": r.reason,
                    "recorded_at": r.recorded_at,
                    "superseded_by": r.superseded_by,
                }
                for r in self.revisions
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Fact":
        fact = cls(
            statement=data["statement"],
            source_pointer=SourcePointer.from_json(data["source_pointer"]),
            staleness_class=data["staleness_class"],
            fact_id=data["fact_id"],
            recorded_at=data["recorded_at"],
            state=data["state"],
            superseded_by=data.get("superseded_by"),
        )
        fact.revisions = [
            Revision(
                action=r["action"],
                actor_id=r["actor_id"],
                reason=r["reason"],
                recorded_at=r["recorded_at"],
                superseded_by=r.get("superseded_by"),
            )
            for r in data.get("revisions", [])
        ]
        return fact


def apply_replacement(old: Fact, new_fact_id: str, actor_id: str, reason: str) -> None:
    """Mark `old` as replaced by `new_fact_id`. Additive: keeps the old record intact.

    Raises ConflictStateError if `old` is not in_force (in-force-only rule, #11740).
    """
    if not old.in_force:
        raise ConflictStateError(
            f"only an in_force fact may be replaced (fact {old.fact_id} is {old.state!r})"
        )
    old.state = "replaced"
    old.superseded_by = new_fact_id
    old.revisions.append(
        Revision(
            action="replaced",
            actor_id=actor_id,
            reason=reason,
            recorded_at=_now(),
            superseded_by=new_fact_id,
        )
    )


def apply_retirement(old: Fact, actor_id: str, reason: str) -> None:
    """Retire `old`. Additive: the record and why it was retired stay on the ledger.

    Raises ConflictStateError if `old` is not in_force (in-force-only rule, #11740).
    """
    if not old.in_force:
        raise ConflictStateError(
            f"only an in_force fact may be retired (fact {old.fact_id} is {old.state!r})"
        )
    old.state = "retired"
    old.revisions.append(
        Revision(action="retired", actor_id=actor_id, reason=reason, recorded_at=_now())
    )
