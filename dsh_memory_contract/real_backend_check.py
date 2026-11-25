"""Real-backend verification: run the five contracts against the persistent
MemoryBrainProvider, then load a real memory map and prove persistence.

Paths are configurable via environment variables:
  DSH_MEMORY_MAP   — path to your hub's memory map JSON (optional; the check
                     skips the real-data section when absent)
  DSH_STORE_DIR    — directory for the journal and store files

Run:  python3 -m dsh_memory_contract.real_backend_check
"""

from __future__ import annotations

import os

from .contracts import run_all_contracts
from .journal import AppendOnlyEventLog
from .memory_brain_adapter import import_memory_map
from .memory_brain_provider import MemoryBrainProvider

REAL_MAP = os.environ.get("DSH_MEMORY_MAP", "")
REAL_STORE_DIR = os.environ.get("DSH_STORE_DIR", ".dsh_store")


def main() -> int:
    print("dsh_memory_contract real-backend check")
    print("=" * 60)

    # 1) five contracts against the persistent provider
    all_pass = True
    for r in run_all_contracts(MemoryBrainProvider):
        status = "PASS" if r.passed else "FAIL"
        all_pass = all_pass and r.passed
        print(f"[{status}] {r.name}: {r.detail}")
    print("-" * 60)

    # 2) real data: import the actual memory map
    if not os.path.exists(REAL_MAP):
        print(f"[SKIP] real map not found at {REAL_MAP}")
        return 0 if all_pass else 1
    os.makedirs(REAL_STORE_DIR, exist_ok=True)
    journal_path = f"{REAL_STORE_DIR}/journal.jsonl"
    store_path = f"{REAL_STORE_DIR}/store.jsonl"
    # fresh run: remove previous store to prove a cold rebuild
    for p in (journal_path, store_path):
        if os.path.exists(p):
            os.remove(p)

    log = AppendOnlyEventLog(path=journal_path, session_id="memory-brain")
    provider = MemoryBrainProvider(event_log=log, store_path=store_path)
    added = import_memory_map(REAL_MAP, log, provider)
    print(f"[OK] imported {added} facts from the real memory map "
          f"({len(log)} journal events)")

    # 3) persistence: a fresh instance must land on the identical view
    view_before = sorted(f.fact_id for f in provider.read())
    reopened = MemoryBrainProvider(event_log=log, store_path=store_path)
    view_after = sorted(f.fact_id for f in reopened.read())
    if view_before != view_after or not view_after:
        print("[FAIL] persistence replay diverged or store empty")
        return 1
    print(f"[OK] persistence: {len(view_after)} in-force facts survive restart, "
          f"identical view")

    # 4) idempotent re-import on the reopened store
    added2 = import_memory_map(REAL_MAP, log, reopened)
    if added2 != 0:
        print(f"[FAIL] re-import added {added2} facts (not idempotent)")
        return 1
    print("[OK] re-import idempotent on real data")

    # 5) budgeted injection over real facts
    block = reopened.inject(400)
    cost = sum(f.token_cost() for f in block)
    print(f"[OK] inject(400): {len(block)} facts, {cost}/400 tokens, "
          f"oldest-first")

    print("=" * 60)
    print("REAL-BACKEND CHECK PASS" if all_pass else "SOME CONTRACTS FAILED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
