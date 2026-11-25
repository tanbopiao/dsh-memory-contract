"""dsh_memory_contract — append-only event log and provider interface.

The journal is the authority (RFC principle 4): sessions may crash, be forked
or abandoned; ingestion is out-of-band and idempotent. Pure standard library.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .model import ContractError, Fact, SourcePointer


class AppendOnlyEventLog:
    """Minimal JSONL append-only log. Each line is one event; offsets are line numbers.

    Supports the operations the contract suite needs: append, resolve (does
    (session_id, event_offset) exist?), read-back, and fork (child log inherits
    a consistent prefix).
    """

    def __init__(self, path: str | None = None, session_id: str = "s0",
                 _inherited_sessions: set[str] | None = None) -> None:
        self.path = path
        self.session_id = session_id
        # sessions whose events this log can resolve (own + inherited via fork)
        self._resolvable_sessions = {session_id} | (_inherited_sessions or set())
        self._events: list[dict[str, Any]] = []
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        event = json.loads(line)
                        self._events.append(event)
                        # a reopened log must keep resolving pointers from
                        # every session already present in the file
                        self._resolvable_sessions.add(event.get("session_id", session_id))

    def append(self, event_type: str, payload: dict[str, Any]) -> int:
        """Append an event; returns its offset (0-based line number)."""
        event = {
            "session_id": self.session_id,
            "offset": len(self._events),
            "event_type": event_type,
            "payload": payload,
        }
        self._events.append(event)
        if self.path:
            line = json.dumps(event, ensure_ascii=False)
            # append atomically: write + flush; JSONL append-only
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return event["offset"]

    def resolves(self, pointer: SourcePointer) -> bool:
        """True iff (session_id, event_offset) names a real event in this log.

        A fork resolves pointers into its own events and into the inherited
        parent prefix (consistent view, RFC contract 5).
        """
        if pointer.session_id not in self._resolvable_sessions:
            return False
        return 0 <= pointer.event_offset < len(self._events)

    def event_at(self, pointer: SourcePointer) -> dict[str, Any] | None:
        if not self.resolves(pointer):
            return None
        return self._events[pointer.event_offset]

    def fork(self, child_session_id: str, path: str | None = None) -> "AppendOnlyEventLog":
        """A fork inherits a consistent view: the full event prefix, under a new session id.

        Provenance pointers into the parent's offsets remain resolvable in the
        child because the child keeps the parent session id in its index.
        """
        child = AppendOnlyEventLog(
            path=path, session_id=child_session_id,
            _inherited_sessions=self._resolvable_sessions,
        )
        child._events = list(self._events)  # consistent prefix
        return child

    def __len__(self) -> int:
        return len(self._events)

    def all_events(self) -> list[dict[str, Any]]:
        return list(self._events)


@runtime_checkable
class MemoryProvider(Protocol):
    """The seam's provider interface. A provider is compliant iff it passes the
    full contract suite (see contracts.py)."""

    def write(self, fact: Fact, source_pointers: list[SourcePointer]) -> str:
        """Persist a fact. MUST reject writes whose source_pointers do not resolve."""
        ...

    def read(self, query: str | None = None) -> list[Fact]:
        """Return in-force facts, optionally filtered. Pull side."""
        ...

    def replace(self, old_id: str, new_fact: Fact, reason: str) -> str:
        """Replace an in_force fact with a new one. Returns the new fact_id."""
        ...

    def retire(self, fact_id: str, reason: str) -> None:
        """Retire an in_force fact."""
        ...

    def inject(self, budget_tokens: int) -> list[Fact]:
        """Return the pushed block: in-force facts within a hard token budget.
        Deterministic for a given store state and budget."""
        ...


@dataclass
class IngestionRecord:
    """One out-of-band ingestion step, for idempotent journal replay."""

    fact: Fact
    log_offset: int
    session_id: str = field(default="s0")


def ingest_journal(provider: "MemoryProvider", log: AppendOnlyEventLog,
                    actor_id: str = "ingestor") -> int:
    """Idempotently ingest every event in `log` into `provider`.

    Replaying the same journal yields the same store (contract 4). Events of
    type "fact_assert" carry a statement; the ingestion derives facts from the
    journal with no LLM in the loop. Returns the number of facts the store
    gained from this pass (0 on a pure replay).
    """
    existing = {f.statement for f in provider.read()}
    added = 0
    for event in log.all_events():
        if event.get("event_type") != "fact_assert":
            continue
        statement = event["payload"].get("statement")
        if not statement or statement in existing:
            continue  # idempotent: replay adds nothing new
        pointer = SourcePointer(session_id=event["session_id"], event_offset=event["offset"])
        # fixed recorded_at: ingestion is deterministic, so a fork ingesting the
        # inherited prefix derives the identical fact_ids as the parent
        fact = Fact(statement=statement, source_pointer=pointer,
                    recorded_at=1700000000.0)
        provider.write(fact, [pointer])
        existing.add(statement)
        added += 1
    return added


def save_store_snapshot(provider: "MemoryProvider") -> list[dict[str, Any]]:
    """Serialize the provider's full store for replay-equality comparison."""
    return [f.to_json() for f in provider.read()]


def stores_equal(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    return sorted(json.dumps(x, sort_keys=True) for x in a) == sorted(
        json.dumps(x, sort_keys=True) for x in b
    )
