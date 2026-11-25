# dsh-memory-contract

A **contract suite** for cross-session memory in [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) — the concrete deliverable proposed in the RFC draft from [Discussion #13749](https://github.com/deepseek-ai/deepseek-harness/discussions/13749).

The premise: two memory providers can both "implement `MemoryProvider`" and still disagree on update semantics, ordering, and rejection. An interface does not make providers interchangeable — **a contract suite does**. This package ships the suite plus a noop reference provider that passes it.

## The five contracts

A provider is compliant iff it passes all of:

| # | Contract | What it enforces |
|---|----------|------------------|
| 1 | **Write traceability** | Every accepted write carries resolvable `source_pointer`s into the append-only event log; unresolvable references are rejected. |
| 2 | **Conflict semantics** | Second write to the same id is deterministic (documented error); `replace`/`retire` only from `in_force` (the #11740 in-force-only rule). |
| 3 | **Injection budget** | The pushed block respects a hard token budget; result is deterministic for a given store state. |
| 4 | **Ingestion replay** | Ingestion from the append-only journal is idempotent; replaying the journal yields the same store. |
| 5 | **Fork consistency** | A fork inherits a consistent view of in-force facts; provenance pointers resolve in the child. |

## Usage

```bash
python3 -m dsh_memory_contract.selftest
```

```python
from dsh_memory_contract import (
    AppendOnlyEventLog, Fact, NoopReferenceProvider, SourcePointer, run_all_contracts
)

log = AppendOnlyEventLog(path="journal.jsonl", session_id="s0")
offset = log.append("fact_assert", {"statement": "port 8080 is configured"})

provider = NoopReferenceProvider(event_log=log)
fact = Fact(statement="port 8080 is configured",
            source_pointer=SourcePointer(session_id="s0", event_offset=offset))
provider.write(fact, [fact.source_pointer])

results = run_all_contracts(NoopReferenceProvider)  # all PASS
```

To certify a custom provider, implement the `MemoryProvider` protocol and run
`run_all_contracts(YourProvider)`.

## Real backend: `MemoryBrainProvider`

`MemoryBrainProvider` is a persistent production counterpart of the noop
reference: identical semantics, plus an append-only JSONL store file that
survives restarts (every mutation is fsynced; a replayed store lands on the
identical in-force view).

```python
from dsh_memory_contract import AppendOnlyEventLog, MemoryBrainProvider, import_memory_map

log = AppendOnlyEventLog(path="journal.jsonl", session_id="memory-brain")
provider = MemoryBrainProvider(event_log=log, store_path="store.jsonl")

# seed from your hub's memory map (idempotent on re-run)
import_memory_map("path/to/memory_map.json", log, provider)
```

Verify everything, including persistence and idempotent re-import on real data:

```bash
DSH_MEMORY_MAP=path/to/memory_map.json \
  python3 -m dsh_memory_contract.real_backend_check
```

The real-data section is skipped when `DSH_MEMORY_MAP` is unset; the five
contracts always run.

## Design notes

- Facts are stored as `(fact, source_pointer, timestamp)` — never free text.
- Three staleness classes (`probe` / `provenance` / `renewal`) because "still true" is not one test.
- Corrections are additive: a dated interpretation with its own reason, never an overwrite.
- The write path is out-of-band journal ingestion — no session-close callbacks, no LLM in the loop.
- Nothing here requires DSH core changes; native adoption is optional, not a precondition.

## License

MIT
