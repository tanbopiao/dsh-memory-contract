"""dsh_memory_contract — memory brain adapter.

Bridges the suite to a real memory brain storage:
- a canonical cross-session ledger (e.g. a MEMORY.md plus ad-hoc notes)
- a central machine-readable map (pointer regime: the hub stores address +
  fingerprint only), with entries of topic/location/owner/verify/status

import_memory_map() seeds an event log with one fact_assert per map entry,
then ingests idempotently into a provider. Re-running against the same map
adds nothing (contract 4 holds on real data too).

Pure standard library, Python 3.10+.
"""

from __future__ import annotations

import hashlib
import json

from .journal import AppendOnlyEventLog, ingest_journal
from .model import Fact, SourcePointer


def _fingerprint(entry: dict) -> str:
    canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def entry_statement(entry: dict) -> str:
    """One-line authoritative statement for a memory map entry."""
    topic = entry.get("topic", "?")
    location = entry.get("location", "?")
    owner = entry.get("owner", "?")
    verify = entry.get("verify", "?")
    status = entry.get("status", "?")
    return f"{topic}: {location} (owner={owner}, verify={verify}, status={status})"


def import_memory_map(map_path: str, log: AppendOnlyEventLog,
                      provider, actor_id: str = "memory_brain_adapter") -> int:
    """Seed the log from the real memory map and ingest into the provider.

    Idempotent: entries already present as in-force statements are skipped.
    Returns the number of facts this pass added.
    """
    with open(map_path, encoding="utf-8") as fh:
        mapping = json.load(fh)
    existing = {f.statement for f in provider.read()}
    added = 0
    for entry in mapping.get("entries", []):
        statement = entry_statement(entry)
        if statement in existing:
            continue
        offset = log.append("fact_assert", {
            "statement": statement,
            "fingerprint": _fingerprint(entry),
            "source": mapping.get("map", "memory_map"),
            "updated_at": entry.get("updated_at"),
        })
        pointer = SourcePointer(session_id=log.session_id, event_offset=offset)
        fact = Fact(statement=statement, source_pointer=pointer,
                    staleness_class="renewal", recorded_at=1700000000.0)
        provider.write(fact, [pointer])
        existing.add(statement)
        added += 1
    # keep the ingest_journal path exercised too (idempotent no-op here,
    # since facts were written with the same statements above)
    ingest_journal(provider, log)
    return added
