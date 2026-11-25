"""dsh_memory_contract — contract suite for the cross-session memory seam.

From the RFC draft in deepseek-ai/deepseek-harness Discussion #13749.
Pure standard library, Python 3.10+.
"""

from .model import (
    STALENESS_CLASSES,
    STATES,
    ConflictStateError,
    ContractError,
    DuplicateFactId,
    Fact,
    Revision,
    SourcePointer,
    UnresolvableSourcePointer,
)
from .journal import AppendOnlyEventLog, MemoryProvider, ingest_journal
from .provider import NoopReferenceProvider
from .memory_brain_provider import MemoryBrainProvider
from .memory_brain_adapter import import_memory_map
from .contracts import run_all_contracts

__version__ = "0.1.0"
__all__ = [
    "STALENESS_CLASSES",
    "STATES",
    "AppendOnlyEventLog",
    "ConflictStateError",
    "ContractError",
    "DuplicateFactId",
    "Fact",
    "MemoryBrainProvider",
    "MemoryProvider",
    "NoopReferenceProvider",
    "import_memory_map",
    "Revision",
    "SourcePointer",
    "UnresolvableSourcePointer",
    "ingest_journal",
    "run_all_contracts",
]
