"""
Semantic-aware LRU cache for Artifacts (Day 14+).

Design goals:
  * Opt-in: never wired in unless ArtifactStore(enable_cache=True).
  * Backward compatible: old on-disk JSON files without cache fields
    round-trip through Artifact.to_dict / from_dict without changes.
  * 4 eviction rules, applied in order of precedence:
        R1. LRU baseline — most-recently-used wins.
        R2. Centrality weighting — high-degree nodes (heavy provenance
            dependents) are protected from eviction.
        R3. Failed artifacts (validation_status == INVALIDATED) are
            NEVER admitted and are evicted on status flip.
        R4. Per-artifact size cap (default 10 MB). Oversized artifacts
            are not cached at all.

This module is deliberately self-contained: it does NOT import
provenance.graph (so loading this file does not pull in the provenance
stack). A `degree_provider` callable is injected by the caller; if not
supplied, every node is treated as degree=0 (pure LRU).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Optional, Tuple

from .schema import Artifact, ArtifactStatus

# Default per-artifact size cap. Anything bigger is too costly to keep
# in-process; callers should re-read from disk.
DEFAULT_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024  # 10 MB

# Default cache-wide budgets. Tunable via ArtifactStore constructor.
DEFAULT_MAX_ENTRIES = 1024
DEFAULT_MAX_BYTES = 256 * 1024 * 1024  # 256 MB

# Type alias for the (run_id, artifact_id, version) cache key.
CacheKey = Tuple[str, str, int]

# Type alias for the optional degree provider. Receives a CacheKey and
# returns the provenance-graph degree of that node (int >= 0). Returning
# 0 makes the cache behave as pure LRU.
DegreeProvider = Callable[[CacheKey], int]


# --------------------------------------------------------------------------- #
# Admission policy
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AdmissionDecision:
    """Result of an admission check.

    `admitted=False` means the artifact is deliberately not placed in
    the cache. The reason is exposed for telemetry / tests.
    """
    admitted: bool
    reason: str = ""


def _estimate_artifact_size(artifact: Artifact) -> int:
    """Approximate in-memory size of an Artifact in bytes.

    We use the JSON-serialized length as a cheap, stable proxy. It
    matches the on-disk footprint closely enough for cache budgeting.
    """
    import json
    return len(json.dumps(artifact.to_dict(), ensure_ascii=False).encode("utf-8"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Eviction policies
# --------------------------------------------------------------------------- #

class EvictionPolicy(ABC):
    """Abstract eviction policy.

    Given a snapshot of the cache, return the key of the next victim.
    Returning None means "no eviction possible" (cache is empty or all
    entries are pinned, in which case the caller may choose to reject
    the insertion).
    """
    @abstractmethod
    def select_victim(
        self,
        entries: "OrderedDict[CacheKey, int]",
        degree_of: DegreeProvider,
    ) -> Optional[CacheKey]: ...


class LRUEvictionPolicy(EvictionPolicy):
    """Pure LRU: victim is the least-recently-used entry.

    `entries` is an OrderedDict ordered from MOST-recently-used (left)
    to LEAST-recently-used (right), matching Python's `move_to_end`
    convention. The rightmost entry is the victim.
    """
    def select_victim(
        self,
        entries: "OrderedDict[CacheKey, int]",
        degree_of: DegreeProvider,
    ) -> Optional[CacheKey]:
        if not entries:
            return None
        # Rightmost = least recently used
        return next(iter(reversed(entries)))


class SemanticLRUEvictionPolicy(EvictionPolicy):
    """LRU weighted by inverse-degree.

    Score for a candidate:
        score = recency_rank - alpha * degree
    where:
        - recency_rank is the position from the right (0 = LRU, larger = fresher)
        - degree is the provenance-graph degree (0 if no provider)
        - alpha controls how strongly degree protects a node (default 4)

    The candidate with the LOWEST score is evicted. This means a high
    degree node effectively gets a "free pass" of `alpha` recency slots
    — a degree-10 node is treated as if it were `alpha * 10 = 40`
    positions more recent than its real LRU position.

    This keeps the cache LRU-faithful for the common case (most nodes
    have degree 0 or low degree) while protecting hot fan-in / fan-out
    nodes (Claims, Metrics with many downstream consumers) from
    thrashing under memory pressure.
    """
    def __init__(self, alpha: float = 4.0) -> None:
        if alpha < 0:
            raise ValueError("alpha must be >= 0")
        self.alpha = alpha

    def select_victim(
        self,
        entries: "OrderedDict[CacheKey, int]",
        degree_of: DegreeProvider,
    ) -> Optional[CacheKey]:
        if not entries:
            return None
        # Compute scores; rightmost entry has recency_rank = 0
        keys = list(entries.keys())  # MRU -> LRU
        n = len(keys)
        best_key: Optional[CacheKey] = None
        best_score: float = float("inf")
        for rank, key in enumerate(keys):
            # rank = 0 is leftmost (MRU); recency from right = n - 1 - rank
            recency = n - 1 - rank
            degree = max(0, int(degree_of(key)))
            # HIGHER score = LESS likely to be evicted. We add
            # alpha * degree so a high-degree node gets a protection
            # bonus. The lowest-score node is the victim.
            score = recency + self.alpha * degree
            if score < best_score:
                best_score = score
                best_key = key
        return best_key


# --------------------------------------------------------------------------- #
# Cache entry
# --------------------------------------------------------------------------- #

class _CacheEntry:
    """Internal bookkeeping for one cached artifact.

    Stored under the cache key. The Artifact itself is held by the
    owning store; the cache only keeps a deep-copy reference plus
    LRU metadata so that callers cannot mutate the cached value
    behind the store's back.
    """
    __slots__ = ("artifact", "size_bytes")

    def __init__(self, artifact: Artifact, size_bytes: int) -> None:
        self.artifact = artifact
        self.size_bytes = size_bytes


# --------------------------------------------------------------------------- #
# Semantic LRU cache
# --------------------------------------------------------------------------- #

class SemanticLRUCache:
    """Thread-safe LRU cache for Artifacts with semantic-aware eviction.

    Not wired into ArtifactStore by default. Construct explicitly and
    pass `cache=...` to ArtifactStore(enable_cache=True, cache=...),
    or let the store build a default one from the constructor args.

    Rules (per the Day 14 spec):
        R1. Baseline LRU.
        R2. Centrality weighting via injected degree provider; if no
            provider is supplied, every node is degree 0 (pure LRU).
        R3. Artifacts with validation_status == INVALIDATED are
            rejected at admission and are evicted when their status
            flips to INVALIDATED.
        R4. Per-artifact size cap (default 10 MB) — oversized
            artifacts are never admitted.
    """
    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        policy: Optional[EvictionPolicy] = None,
        degree_provider: Optional[DegreeProvider] = None,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        if max_artifact_bytes <= 0:
            raise ValueError("max_artifact_bytes must be > 0")

        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_artifact_bytes = max_artifact_bytes
        self._policy: EvictionPolicy = policy or LRUEvictionPolicy()
        self._degree_provider: DegreeProvider = degree_provider or (lambda _k: 0)

        # OrderedDict ordered MRU -> LRU
        self._entries: "OrderedDict[CacheKey, _CacheEntry]" = OrderedDict()
        self._total_bytes = 0
        self._lock = RLock()

        # Telemetry — useful for tests and ops dashboards.
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.admit_rejections = 0

    # ---------- configuration ----------

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def max_artifact_bytes(self) -> int:
        return self._max_artifact_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    # ---------- admission ----------

    def check_admission(self, artifact: Artifact) -> AdmissionDecision:
        """Decide whether `artifact` is eligible for caching.

        Pure check — does NOT mutate cache state. Used by the store
        to decide whether to call `put`.
        """
        # R3: failed artifacts are never cached
        status = artifact.validation_status
        if isinstance(status, ArtifactStatus):
            is_failed = status == ArtifactStatus.INVALIDATED
        else:
            is_failed = str(status) == str(ArtifactStatus.INVALIDATED.value)
        if is_failed:
            return AdmissionDecision(False, "R3: failed artifact")

        # R4: per-artifact size cap
        size = _estimate_artifact_size(artifact)
        if size > self._max_artifact_bytes:
            return AdmissionDecision(
                False,
                f"R4: size {size} > cap {self._max_artifact_bytes}",
            )
        return AdmissionDecision(True, "ok")

    # ---------- core operations ----------

    def put(self, artifact: Artifact) -> bool:
        """Admit `artifact` to the cache. Returns True on success.

        On False the caller should leave the disk-resident copy as the
        source of truth (the cache simply is not helping).
        """
        decision = self.check_admission(artifact)
        if not decision.admitted:
            self.admit_rejections += 1
            return False

        key: CacheKey = (artifact.run_id, artifact.artifact_id, artifact.version)
        size = _estimate_artifact_size(artifact)

        with self._lock:
            # If already present, drop the old size and re-insert as MRU.
            if key in self._entries:
                self._total_bytes -= self._entries[key].size_bytes
                del self._entries[key]

            # Make room until we're under both budgets.
            while self._needs_eviction(size):
                victim = self._policy.select_victim(
                    self._entries, self._degree_provider
                )
                if victim is None:
                    # Cache is empty but we still don't fit: artifact
                    # is bigger than the whole cache. Reject admission.
                    self.admit_rejections += 1
                    return False
                self._evict(victim)

            self._entries[key] = _CacheEntry(artifact.model_copy(deep=True), size)
            self._entries.move_to_end(key, last=False)  # mark as MRU
            self._total_bytes += size
            return True

    def get(self, key: CacheKey) -> Optional[Artifact]:
        """Return a deep-copy of the cached artifact, marking it MRU.

        Returns None on miss. The returned copy is safe to mutate.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key, last=False)  # MRU
            self.hits += 1
            return entry.artifact.model_copy(deep=True)

    def invalidate(self, key: CacheKey) -> bool:
        """Remove `key` from the cache. Returns True if it was present."""
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return False
            self._total_bytes -= entry.size_bytes
            return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0

    def contains(self, key: CacheKey) -> bool:
        with self._lock:
            return key in self._entries

    # ---------- internals ----------

    def _needs_eviction(self, incoming_size: int) -> bool:
        if len(self._entries) >= self._max_entries:
            return True
        if self._total_bytes + incoming_size > self._max_bytes:
            return True
        return False

    def _evict(self, key: CacheKey) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        self._total_bytes -= entry.size_bytes
        self.evictions += 1

    # ---------- bookkeeping helpers (used by store) ----------

    def touch_on_get(self, artifact: Artifact) -> None:
        """Update access_count / last_access_at on the cached copy.

        This is called by ArtifactStore.get() on a cache hit. It mutates
        the cached copy only — the disk copy is left for the store to
        persist on its own schedule.
        """
        key: CacheKey = (artifact.run_id, artifact.artifact_id, artifact.version)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.artifact.access_count = artifact.access_count
            entry.artifact.last_access_at = artifact.last_access_at

    def key_of(self, artifact: Artifact) -> CacheKey:
        return (artifact.run_id, artifact.artifact_id, artifact.version)
