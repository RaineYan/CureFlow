"""
Built-in recovery strategies.

Importing this package is the side-effecting way to populate the
global `recovery.strategy.REGISTRY` with the three Day-9/10
strategies.  Tests that want a clean slate can do
`REGISTRY.clear()` and re-import the package.
"""
from __future__ import annotations

# Importing each module triggers the @register_strategy decorator
# at import time, populating the global registry.
from . import (  # noqa: F401  (side-effecting imports)
    coarse_checkpoint,
    full_rerun,
    selective_recovery,
)
from .coarse_checkpoint import CoarseCheckpointStrategy
from .full_rerun import FullRerunStrategy
from .selective_recovery import SelectiveRecoveryStrategy


__all__ = [
    "CoarseCheckpointStrategy",
    "FullRerunStrategy",
    "SelectiveRecoveryStrategy",
]
