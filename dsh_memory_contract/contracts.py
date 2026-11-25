"""dsh_memory_contract — the five-contract suite.

A provider is compliant iff run_all_contracts(provider_factory) returns all
PASS. Each contract is independently runnable.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass

from .journal import AppendOnlyEventLog, ingest_journal, stores_equal
from .model import (
    ConflictStateError,
    ContractError,
    DuplicateFactId,
    Fact,
    SourcePointer,
    UnresolvableSourcePointer,
)
from .provider import NoopReferenceProvider


@dataclass
class ContractResult:
    name: str
    passed: bool
    detail: str


def _make_fact(statement: str, session_id: str = "s0", offset: int = 0,
               staleness: str = "provenance") -> Fact:
    # fixed recorded_at keeps derived fact_ids deterministic for identical input
    return Fact(
        statement=statement,
        source_pointer=SourcePointer(session_id=session_id, event_offset=offset),
        staleness_class=staleness,
        recorded_at=1700000000.0,
    )


# Contract 1: Write — every accepted write carries resolvable source pointers;
# unresolvable references are rejected.
def contract_write_traceability(provider_factory) -> ContractResult:
    try:
        log = AppendOnlyEventLog(session_id="s0")
        log.append("session_start", {})
        provider = provider_factory(event_log=log)
        good = _make_fact("deploy uses v2.1", offset=0)
        provider.write(good, [good.source_pointer])
        bad = _make_fact("ghost fact", offset=99)
        try:
            provider.write(bad, [bad.source_pointer])
        except UnresolvableSourcePointer:
            return ContractResult("write_traceability", True,
                                  "unresolvable pointer rejected; resolvable accepted")
        return ContractResult("write_traceability", False,
                              "unresolvable pointer was NOT rejected")
    except Exception as exc:  # noqa: BLE001
        return ContractResult("write_traceability", False, f"unexpected error: {exc}")


# Contract 2: Conflict — second write to the same id is deterministic (error,
# documented); replace/retire only from in_force.
def contract_conflict_semantics(provider_factory) -> ContractResult:
    try:
        log = AppendOnlyEventLog(session_id="s0")
        log.append("session_start", {})
        provider = provider_factory(event_log=log)
        f1 = _make_fact("prefers dark theme", offset=0)
        provider.write(f1, [f1.source_pointer])
        # deterministic second-write behavior: must raise DuplicateFactId
        try:
            provider.write(_make_fact("prefers dark theme", offset=0), [f1.source_pointer])
            return ContractResult("conflict_semantics", False,
                                  "duplicate id write did not raise")
        except DuplicateFactId:
            pass
        except ContractError:
            return ContractResult("conflict_semantics", False,
                                  "duplicate id raised wrong error class")
        # replace an in_force fact: OK
        f2 = _make_fact("prefers light theme", offset=0)
        new_id = provider.replace(f1.fact_id, f2, reason="user changed preference")
        # replacing an already-replaced fact must fail (in-force-only rule)
        f3 = _make_fact("prefers system theme", offset=0)
        try:
            provider.replace(f1.fact_id, f3, reason="double replace")
            return ContractResult("conflict_semantics", False,
                                  "replaced fact accepted another replace")
        except ConflictStateError:
            pass
        # retire only in_force: f2 is in_force -> ok; f1 is replaced -> error
        provider.retire(new_id, reason="obsolete")
        try:
            provider.retire(new_id, reason="double retire")
            return ContractResult("conflict_semantics", False,
                                  "retired fact accepted another retire")
        except ConflictStateError:
            pass
        return ContractResult("conflict_semantics", True,
                              "duplicate id deterministic; in-force-only rule enforced")
    except Exception as exc:  # noqa: BLE001
        return ContractResult("conflict_semantics", False, f"unexpected error: {exc}")


# Contract 3: Injection — pushed block respects a hard token budget; result is
# deterministic for a given store state.
def contract_injection_budget(provider_factory) -> ContractResult:
    try:
        log = AppendOnlyEventLog(session_id="s0")
        log.append("session_start", {})
        provider = provider_factory(event_log=log)
        for i in range(5):
            f = _make_fact(f"fact number {i} with some padding text", offset=0)
            provider.write(f, [f.source_pointer])
        budget = 20  # tokens; each fact ~10 tokens -> expect 2 facts
        block1 = provider.inject(budget)
        block2 = provider.inject(budget)
        total = sum(f.token_cost() for f in block1)
        if total > budget:
            return ContractResult("injection_budget", False,
                                  f"block cost {total} exceeds budget {budget}")
        if [f.fact_id for f in block1] != [f.fact_id for f in block2]:
            return ContractResult("injection_budget", False, "inject is not deterministic")
        if not block1:
            return ContractResult("injection_budget", False,
                                  "budget large enough but block empty")
        return ContractResult("injection_budget", True,
                              f"block of {len(block1)} facts, {total}/{budget} tokens, deterministic")
    except Exception as exc:  # noqa: BLE001
        return ContractResult("injection_budget", False, f"unexpected error: {exc}")


# Contract 4: Ingestion — journal replay is idempotent; replaying the same
# journal yields the same store.
def contract_ingestion_replay(provider_factory) -> ContractResult:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/journal.jsonl"
            log = AppendOnlyEventLog(path=path, session_id="s0")
            log.append("session_start", {})
            log.append("fact_assert", {"statement": "port 8080 is configured"})
            log.append("fact_assert", {"statement": "chose X on 2026-11-20"})
            log.append("fact_assert", {"statement": "port 8080 is configured"})  # duplicate stmt
            # first pass on a fresh provider
            p1 = provider_factory(event_log=AppendOnlyEventLog(path=path, session_id="s0"))
            added1 = ingest_journal(p1, log)
            snap1 = stores_equal(
                [f.to_json() for f in p1.read()], [f.to_json() for f in p1.read()]
            )
            # second pass on the SAME provider: pure replay must add nothing
            added2 = ingest_journal(p1, log)
            # third pass on a FRESH provider: must converge to the same store
            p2 = provider_factory(event_log=AppendOnlyEventLog(path=path, session_id="s0"))
            ingest_journal(p2, log)
            same = stores_equal(
                [f.to_json() for f in p1.read()], [f.to_json() for f in p2.read()]
            )
            if not snap1:
                return ContractResult("ingestion_replay", False, "store not self-consistent")
            if added2 != 0:
                return ContractResult("ingestion_replay", False,
                                      f"replay on same store added {added2} facts (not idempotent)")
            if not same:
                return ContractResult("ingestion_replay", False,
                                      "fresh replay diverged from original store")
            if added1 != 2:  # duplicate statement must be deduped
                return ContractResult("ingestion_replay", False,
                                      f"expected 2 unique facts, got {added1}")
            return ContractResult("ingestion_replay", True,
                                  "replay idempotent; fresh replay converges; dupes deduped")
    except Exception as exc:  # noqa: BLE001
        return ContractResult("ingestion_replay", False, f"unexpected error: {exc}")


# Contract 5: Fork — a fork inherits a consistent view of in-force facts and
# provenance pointers resolve in the child.
def contract_fork_consistency(provider_factory) -> ContractResult:
    try:
        log = AppendOnlyEventLog(session_id="s0")
        log.append("session_start", {})
        log.append("fact_assert", {"statement": "chose X on 2026-11-20"})
        parent = provider_factory(event_log=log)
        f = _make_fact("chose X on 2026-11-20", offset=1)
        parent.write(f, [f.source_pointer])
        # fork the log; the child provider ingests the inherited journal
        # (out-of-band, idempotent — RFC principle 4) and must land on the
        # same in-force view as the parent at the fork point
        child_log = log.fork("s1")
        child = provider_factory(event_log=child_log)
        ingest_journal(child, child_log)
        parent_view = sorted(x.fact_id for x in parent.read())
        child_view = sorted(x.fact_id for x in child.read())
        if parent_view != child_view:
            return ContractResult("fork_consistency", False,
                                  "child view diverged from parent at fork point")
        # provenance pointer must resolve in the child's log index
        for fact in child.read():
            if not child_log.resolves(fact.source_pointer):
                return ContractResult("fork_consistency", False,
                                      f"pointer {fact.source_pointer.as_tuple()} unresolved in child")
        return ContractResult("fork_consistency", True,
                              "child inherits consistent in-force view; pointers resolve")
    except Exception as exc:  # noqa: BLE001
        return ContractResult("fork_consistency", False, f"unexpected error: {exc}")


CONTRACTS = [
    contract_write_traceability,
    contract_conflict_semantics,
    contract_injection_budget,
    contract_ingestion_replay,
    contract_fork_consistency,
]


def run_all_contracts(provider_factory=NoopReferenceProvider) -> list[ContractResult]:
    return [c(provider_factory) for c in CONTRACTS]
