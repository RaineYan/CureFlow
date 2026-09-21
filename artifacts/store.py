"""
ArtifactStore — JSON file backend, run-scoped, version-aware.

Layout on disk:
    <base_dir>/
        <run_id>/
            <artifact_id>__v<version>.json
            ...

In-memory index:
    _by_ref : Dict[(run_id, artifact_id, version)] -> Artifact
    _by_run : Dict[run_id] -> List[artifact_id]   (distinct ids, insertion order)

Public API (Day 1 revised — every call carries run_id):

    save(artifact) -> Artifact
        Persist. (run_id, artifact_id, version) must be unique.
        Raises ValueError on duplicate.

    get(run_id, artifact_id, version=None) -> Artifact | None
        Latest content version if version=None, else exact version.

    get_version(run_id, artifact_id, version) -> Artifact | None
        Exact version lookup (also reachable via get(..., version=N)).

    list_versions(run_id, artifact_id) -> list[int]
        All version numbers for a (run, artifact), sorted ascending.

    list_by_run(run_id) -> list[Artifact]
        Latest content version of every artifact in the run.

    list_all() -> list[Artifact]
        Latest content version of every artifact across all runs.

    list_ids_by_run(run_id) -> list[str]

    update_status(run_id, artifact_id, new_status) -> Artifact
        Mutate validation_status of the latest content version.
        Does NOT increment version. Rewrites the same JSON file.
        Raises KeyError if (run_id, artifact_id) not found.

    reload() -> None
        Rebuild in-memory index from on-disk JSON files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import List, Optional

from .cache import (
    DEFAULT_MAX_ARTIFACT_BYTES,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_MAX_BYTES,
    CacheKey,
    DegreeProvider,
    SemanticLRUCache,
)
from .schema import Artifact, ArtifactStatus


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactStore:
    def __init__(
        self,
        base_dir: str = "artifact_data",
        enable_cache: bool = False,
        cache_max_entries: int = DEFAULT_MAX_ENTRIES,
        cache_max_bytes: int = DEFAULT_MAX_BYTES,
        cache_max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        cache: Optional[SemanticLRUCache] = None,
        degree_provider: Optional[DegreeProvider] = None,
    ):
        """
        Args:
            base_dir: Root directory for on-disk JSON files.
            enable_cache: Default False. The store's public API is
                bit-for-bit unchanged when False (no cache is built,
                no extra code paths run, no telemetry counter
                touched). Flip to True to opt into the
                semantic-aware LRU cache (Day 14+).
            cache_max_entries / cache_max_bytes / cache_max_artifact_bytes:
                Used only when `cache` is None and `enable_cache` is
                True. Passed through to the default SemanticLRUCache.
            cache: Inject a pre-built cache. Takes precedence over the
                `cache_max_*` arguments. Caller is responsible for
                thread-safety; the store only acquires its own lock
                around the cache via the cache's public methods.
            degree_provider: Optional callable used by the cache to
                look up the provenance-graph degree of a node. If
                None, the cache treats every node as degree 0
                (pure LRU).
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._by_ref: dict[tuple[str, str, int], Artifact] = {}
        self._by_run: dict[str, list[str]] = {}

        # Cache is OPT-IN. Default off: existing callers see zero
        # behavior change. The cache object exists iff enable_cache.
        self._cache: Optional[SemanticLRUCache] = None
        if enable_cache:
            if cache is not None:
                self._cache = cache
            else:
                self._cache = SemanticLRUCache(
                    max_entries=cache_max_entries,
                    max_bytes=cache_max_bytes,
                    max_artifact_bytes=cache_max_artifact_bytes,
                    degree_provider=degree_provider,
                )

    @property
    def cache(self) -> Optional[SemanticLRUCache]:
        """The active cache, or None if the store was built with cache off."""
        return self._cache

    # ---------- disk path ----------

    def _path_for(self, run_id: str, artifact_id: str, version: int) -> Path:
        run_dir = self.base_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir / f"{artifact_id}__v{version}.json"

    def _write(self, artifact: Artifact) -> None:
        path = self._path_for(artifact.run_id, artifact.artifact_id, artifact.version)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(artifact.to_dict(), f, ensure_ascii=False, indent=2)

    # ---------- core write ----------

    def save(self, artifact: Artifact) -> Artifact:
        """Persist artifact. (run_id, artifact_id, version) must be unique.

        Raises ValueError on duplicate key. Caller is responsible for
        choosing the version number (1 for first creation, n+1 for
        recovery / recompute).

        When the store has a cache enabled, successful saves also
        admit the artifact to the cache (subject to admission rules:
        R3 skips INVALIDATED, R4 skips oversized).
        """
        key = (artifact.run_id, artifact.artifact_id, artifact.version)
        with self._lock:
            if key in self._by_ref:
                raise ValueError(
                    f"Duplicate (run_id, artifact_id, version): {key}. "
                    "If you want a new version, increment `version`."
                )
            self._write(artifact)
            self._by_ref[key] = artifact
            ids = self._by_run.setdefault(artifact.run_id, [])
            if artifact.artifact_id not in ids:
                ids.append(artifact.artifact_id)
            # Cache admission (opt-in). Done inside the same lock so
            # the cache and the in-memory index stay consistent under
            # concurrent writers. Admission check itself may reject
            # the artifact (R3/R4) — that is a silent no-op.
            if self._cache is not None:
                self._cache.put(artifact)
        return artifact

    # ---------- core read ----------

    def get(
        self,
        run_id: str,
        artifact_id: str,
        version: Optional[int] = None,
    ) -> Optional[Artifact]:
        """Return latest content version if version=None, else exact version.

        Day 4 fix: returns a deep copy so external mutation cannot leak
        back into the store. Equivalent to:
            return self._by_ref[key].model_copy(deep=True)
        but slightly faster because it skips a re-validate pass.

        When the cache is enabled, a hit also bumps `access_count` and
        refreshes `last_access_at` on BOTH the cached copy (for
        eviction policy) and the in-memory `_by_ref` copy (so the
        on-disk write on the next save() reflects the latest counter).
        """
        cache = self._cache
        if cache is not None:
            # Fast path: if the caller asked for an exact version AND
            # the cache has it, serve from cache and refresh LRU.
            if version is not None:
                cache_key: CacheKey = (run_id, artifact_id, version)
                cached = cache.get(cache_key)
                if cached is not None:
                    now = _utc_now_iso()
                    cached.access_count += 1
                    cached.last_access_at = now
                    # Mirror onto the in-memory index so a subsequent
                    # save() persists the new counter. We mutate
                    # _by_ref directly (not through deep copy) so the
                    # next get() sees the updated counter; the deep
                    # copy is only on the way OUT.
                    with self._lock:
                        ref_copy = self._by_ref[cache_key]
                        ref_copy.access_count = cached.access_count
                        ref_copy.last_access_at = cached.last_access_at
                    cache.touch_on_get(cached)
                    return cached

        key = self._resolve_key(run_id, artifact_id, version)
        if key is None:
            return None
        with self._lock:
            ref = self._by_ref[key]
            # Cache bookkeeping is opt-in. When the cache is off we
            # preserve the pre-Day-14 behavior bit-for-bit: the
            # returned artifact is exactly what the original get()
            # would have returned (no access_count bump, no
            # last_access_at touch on the in-memory copy).
            if cache is not None:
                ref.access_count += 1
                ref.last_access_at = _utc_now_iso()
            out = ref.model_copy(deep=True)
        # Warm the cache on this read so the next get hits it. We do
        # this AFTER releasing the lock to keep the critical section
        # short. put() acquires its own internal lock.
        if cache is not None:
            cache.put(out)
            # put() made a deep copy inside the cache; mirror the new
            # counters onto it so eviction sees the fresh LRU data.
            cache.touch_on_get(out)
        return out

    def _resolve_key(
        self,
        run_id: str,
        artifact_id: str,
        version: Optional[int],
    ) -> Optional[tuple[str, str, int]]:
        if version is not None:
            return (run_id, artifact_id, version) if (run_id, artifact_id, version) in self._by_ref else None
        max_v: Optional[int] = None
        for (r, a, v) in self._by_ref:
            if r == run_id and a == artifact_id:
                if max_v is None or v > max_v:
                    max_v = v
        if max_v is None:
            return None
        return (run_id, artifact_id, max_v)

    def get_version(
        self,
        run_id: str,
        artifact_id: str,
        version: int,
    ) -> Optional[Artifact]:
        """Return a specific version. Day 4 fix: deep copy on the way out."""
        a = self._by_ref.get((run_id, artifact_id, version))
        return a.model_copy(deep=True) if a is not None else None

    def list_versions(self, run_id: str, artifact_id: str) -> List[int]:
        return sorted(
            v for (r, a, v) in self._by_ref if r == run_id and a == artifact_id
        )

    def list_by_run(self, run_id: str) -> List[Artifact]:
        out: list[Artifact] = []
        for aid in self._by_run.get(run_id, []):
            latest = self.get(run_id, aid)
            if latest is not None:
                out.append(latest)
        return out

    def list_all_versions_by_run(self, run_id: str) -> List[Artifact]:
        """Return EVERY content version of every artifact in the run.

        Day 4 spec: graph must include history, not just latest. The
        ArtifactGraph builder consumes this so v1 and v2 of the same
        artifact_id appear as distinct nodes.
        """
        out: list[Artifact] = []
        for (r, _a, _v), artifact in self._by_ref.items():
            if r == run_id:
                out.append(artifact.model_copy(deep=True))
        return out

    def list_all(self) -> List[Artifact]:
        seen: set[tuple[str, str]] = set()
        out: list[Artifact] = []
        for (r, a, _v) in self._by_ref:
            key = (r, a)
            if key in seen:
                continue
            seen.add(key)
            latest = self.get(r, a)
            if latest is not None:
                out.append(latest)
        return out

    def list_ids_by_run(self, run_id: str) -> List[str]:
        return list(self._by_run.get(run_id, []))

    # ---------- status mutation ----------

    def update_status(
        self,
        run_id: str,
        artifact_id: str,
        new_status: ArtifactStatus,
    ) -> Artifact:
        """Update validation_status of the latest content version.

        Does NOT increment version. The latest content version's JSON
        file is rewritten in place with the new status field. The
        previous on-disk file at the same version is overwritten.

        Raises KeyError if (run_id, artifact_id) is not present.

        Cache interaction (opt-in):
          * If new_status == INVALIDATED, the entry is evicted from
            the cache (R3: failed artifacts do not live in cache).
          * For any other status, the updated artifact replaces the
            cached entry so subsequent reads see the new status.
        """
        with self._lock:
            latest = self.get(run_id, artifact_id)
            if latest is None:
                raise KeyError(f"Artifact not found: ({run_id}, {artifact_id})")
            # Day 4 fix: deep=True so updating the returned artifact can
            # never leak a field mutation back into _by_ref via shared
            # nested objects (input_artifact_ids list, ExecutionCost, etc.).
            updated = latest.model_copy(
                update={"validation_status": new_status},
                deep=True,
            )
            self._write(updated)
            self._by_ref[(run_id, artifact_id, latest.version)] = updated
            # R3: evict failed artifacts from cache. R3 also forbids
            # re-admitting an INVALIDATED artifact via the cache (the
            # cache's admission check would reject it anyway).
            if self._cache is not None:
                key = (run_id, artifact_id, latest.version)
                normalized = (
                    new_status
                    if isinstance(new_status, ArtifactStatus)
                    else ArtifactStatus(str(new_status))
                )
                if normalized == ArtifactStatus.INVALIDATED:
                    self._cache.invalidate(key)
                else:
                    self._cache.put(updated)
        return updated

    # ---------- recovery from disk ----------

    def reload(self) -> None:
        """Rebuild the in-memory index from on-disk JSON files.

        Multiple versions of the same (run_id, artifact_id) are kept
        independently in _by_ref. list_by_run / get default to the
        highest version.

        Cache interaction (opt-in): the cache is cleared on reload,
        because the in-memory references it holds may be stale. This
        matches the pre-cache invariant: reload() returns the store
        to a known state from disk.
        """
        with self._lock:
            self._by_ref.clear()
            self._by_run.clear()
            if self._cache is not None:
                # Clear outside the cache's own lock would race with
                # concurrent readers. The cache uses its own RLock
                # so this is safe.
                self._cache.clear()

            if not self.base_dir.exists():
                return

            for run_dir in sorted(self.base_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                run_id = run_dir.name
                ids: list[str] = []
                for json_file in sorted(run_dir.glob("*.json")):
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    artifact = Artifact.from_dict(data)
                    key = (run_id, artifact.artifact_id, artifact.version)
                    self._by_ref[key] = artifact
                    if artifact.artifact_id not in ids:
                        ids.append(artifact.artifact_id)
                if ids:
                    self._by_run[run_id] = ids
