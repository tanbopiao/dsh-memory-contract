"""dsh_memory_contract — persistent real-backend provider.

MemoryBrainProvider is the production counterpart of NoopReferenceProvider:
same documented semantics, plus durable persistence. The store is an
append-only JSONL file of store events (fact_written / fact_replaced /
fact_retired); a fresh instance replays the file and lands on the identical
in-force view. The journal remains the authority for source pointers
(RFC principle 4).

Pure standard library, Python 3.10+.
"""

from __future__ import annotations

import json
import os

from .model import (
    ConflictStateError,
    ContractError,
    DuplicateFactId,
    Fact,
    SourcePointer,
    UnresolvableSourcePointer,
    apply_replacement,
    apply_retirement,
)
from .provider import NoopReferenceProvider  # noqa: F401  (re-export convenience)


class MemoryBrainProvider:
    """Persistent provider. Documented semantics (same as the noop reference):

    - Second write with the same fact_id is a deterministic ERROR
      (DuplicateFactId). Updates go through replace(), never implicit overwrite.
    - replace/retire only from in_force (ConflictStateError otherwise).
    - inject() orders in-force facts by recorded_at ascending, oldest first,
      and stops at the first budget overflow (hard budget, deterministic).
    - Persistence: every mutation is appended to the store file and fsynced;
      a replayed store is byte-identical in fact view.
    """

    def __init__(self, event_log=None, store_path: str | None = None) -> None:
        self._log = event_log
        self._store_path = store_path
        self._store: dict[str, Fact] = {}
        if store_path and os.path.exists(store_path):
            self._replay()

    # -- persistence ---------------------------------------------------------

    def _replay(self) -> None:
        with open(self._store_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                action = event["action"]
                if action == "fact_written":
                    fact = Fact.from_json(event["fact"])
                    self._store[fact.fact_id] = fact
                elif action == "fact_replaced":
                    old = self._store[event["old_id"]]
                    new_fact = Fact.from_json(event["fact"])
                    apply_replacement(old, new_fact.fact_id,
                                      actor_id=event["actor_id"],
                                      reason=event["reason"])
                    self._store[new_fact.fact_id] = new_fact
                elif action == "fact_retired":
                    fact = self._store[event["fact_id"]]
                    apply_retirement(fact, actor_id=event["actor_id"],
                                     reason=event["reason"])
                else:  # unknown action: fail loudly, never silently skip
                    raise ContractError(f"unknown store event {action!r}")

    def _persist(self, event: dict) -> None:
        if not self._store_path:
            return
        with open(self._store_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- write side ---------------------------------------------------------

    def write(self, fact: Fact, source_pointers: list[SourcePointer]) -> str:
        if fact.fact_id in self._store:
            raise DuplicateFactId(
                f"fact_id {fact.fact_id} already exists; use replace() for updates"
            )
        for p in source_pointers:
            if self._log is not None and not self._log.resolves(p):
                raise UnresolvableSourcePointer(
                    f"source_pointer {p.as_tuple()} does not resolve in the event log"
                )
        self._store[fact.fact_id] = fact
        self._persist({"action": "fact_written", "fact": fact.to_json()})
        return fact.fact_id

    def replace(self, old_id: str, new_fact: Fact, reason: str) -> str:
        old = self._store.get(old_id)
        if old is None:
            raise ContractError(f"unknown fact_id {old_id!r}")
        apply_replacement(old, new_fact.fact_id, actor_id="memory_brain", reason=reason)
        self._store[new_fact.fact_id] = new_fact
        self._persist({"action": "fact_replaced", "old_id": old_id,
                       "fact": new_fact.to_json(),
                       "actor_id": "memory_brain", "reason": reason})
        return new_fact.fact_id

    def retire(self, fact_id: str, reason: str) -> None:
        fact = self._store.get(fact_id)
        if fact is None:
            raise ContractError(f"unknown fact_id {fact_id!r}")
        apply_retirement(fact, actor_id="memory_brain", reason=reason)
        self._persist({"action": "fact_retired", "fact_id": fact_id,
                       "actor_id": "memory_brain", "reason": reason})

    # -- read side ----------------------------------------------------------

    def read(self, query: str | None = None) -> list[Fact]:
        facts = [f for f in self._store.values() if f.in_force]
        if query:
            q = query.lower()
            facts = [f for f in facts if q in f.statement.lower()]
        return sorted(facts, key=lambda f: (f.recorded_at, f.fact_id))

    def inject(self, budget_tokens: int) -> list[Fact]:
        if budget_tokens < 0:
            raise ContractError("budget_tokens must be >= 0")
        selected: list[Fact] = []
        used = 0
        for fact in self.read():
            cost = fact.token_cost()
            if used + cost > budget_tokens:
                break
            selected.append(fact)
            used += cost
        return selected

    # -- introspection ------------------------------------------------------

    def all_facts(self) -> list[Fact]:
        return list(self._store.values())

    def get(self, fact_id: str) -> Fact | None:
        return self._store.get(fact_id)
