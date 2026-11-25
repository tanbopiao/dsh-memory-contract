"""One-shot self-test: run all five contracts against the noop reference provider."""

from __future__ import annotations

from .contracts import run_all_contracts
from .provider import NoopReferenceProvider


def main() -> int:
    results = run_all_contracts(NoopReferenceProvider)
    print("dsh_memory_contract self-test")
    print("=" * 60)
    all_pass = True
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        all_pass = all_pass and r.passed
        print(f"[{status}] {r.name}: {r.detail}")
    print("=" * 60)
    print("ALL CONTRACTS PASS" if all_pass else "SOME CONTRACTS FAILED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
