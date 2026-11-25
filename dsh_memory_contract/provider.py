"""dsh_memory_contract — noop reference provider.

The minimal implementation that passes the full contract suite. In-memory
store; deterministic ordering everywhere (insertion order, then fact_id).
"""

from __future__ import annotations

from .model import (
    ConflictStateError,
    ContractError,
    DuplicateFactId,
    Fact,
    SourcePointer,
    UnresolvableSourcePointer,
    apply_replacement,
    apply_retirement,
    _token_estimate,
)


class NoopReferenceProvider:
    """In-memory reference provider. Documented semantics:

    - Second write with the same fact_id is a deterministic ERROR
      (DuplicateFactId). Updates go through replace(), never implicit overwrite.
    - inject() orders in-force facts by recorded_at ascending (oldest first),
      which is stable and deterministic for a given store state.
    """

    def __init__(self, event_log=None) -> None:
        # event_log: optional AppendOnlyEventLog used to resolve source pointers
        self._log = event_log
        self._store: dict[str, Fact] = {}

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
        return fact.fact_id

    def replace(self, old_id: str, new_fact: Fact, reason: str) -> str:
        old = self._store.get(old_id)
        if old is None:
            raise ContractError(f"unknown fact_id {old_id!r}")
        apply_replacement(old, new_fact.fact_id, actor_id="provider", reason=reason)
        self._store[new_fact.fact_id] = new_fact
        return new_fact.fact_id

    def retire(self, fact_id: str, reason: str) -> None:
        fact = self._store.get(fact_id)
        if fact is None:
            raise ContractError(f"unknown fact_id {fact_id!r}")
        apply_retirement(fact, actor_id="provider", reason=reason)

    # -- read side ----------------------------------------------------------

    def read(self, query: str | None = None) -> list[Fact]:
        facts = [f for f in self._store.values() if f.in_force]
        if query:
            q = query.lower()
            facts = [f for f in facts if q in f.statement.lower()]
        return sorted(facts, key=lambda f: (f.recorded_at, f.fact_id))

    def inject(self, budget_tokens: int) -> list[Fact]:
        """Pushed block: oldest-first in-force facts within the hard budget."""
        if budget_tokens < 0:
            raise ContractError("budget_tokens must be >= 0")
        selected: list[Fact] = []
        used = 0
        for fact in self.read():
            cost = fact.token_cost()
            if used + cost > budget_tokens:
                break  # hard budget: stop at first overflow, keep result deterministic
            selected.append(fact)
            used += cost
        return selected

    # -- introspection ------------------------------------------------------

    def all_facts(self) -> list[Fact]:
        return list(self._store.values())

    def get(self, fact_id: str) -> Fact | None:
        return self._store.get(fact_id)
