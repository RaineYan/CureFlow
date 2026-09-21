"""
Tests for the semantic-aware LRU cache (Day 14+).

Coverage matrix (4 semantic rules + integration):

    R1. LRU baseline
        - test_lru_evicts_least_recently_used
        - test_lru_get_promotes_to_most_recent
        - test_lru_empty_cache_no_victim
        - test_lru_max_entries_limit
        - test_lru_max_bytes_eviction

    R2. Centrality weighting
        - test_semantic_policy_protects_high_degree_node
        - test_semantic_policy_alpha_zero_equals_lru
        - test_semantic_policy_no_degree_provider_uses_zero
        - test_semantic_policy_higher_degree_higher_protection

    R3. Failed artifacts (INVALIDATED) never cached
        - test_invalidated_artifact_not_admitted
        - test_update_status_to_invalidated_evicts_from_cache
        - test_update_status_to_valid_readmits_to_cache
        - test_check_admission_rejects_failed_artifact
        - test_check_admission_accepts_recovered_artifact

    R4. Per-artifact size cap
        - test_oversized_artifact_not_admitted
        - test_max_artifact_bytes_zero_rejected
        - test_artifact_larger_than_total_cache_rejected

    Store integration
        - test_default_cache_off_unchanged_behavior
        - test_enable_cache_admits_on_save
        - test_enable_cache_serves_from_cache_on_get
        - test_enable_cache_evicts_under_pressure
        - test_reload_clears_cache
        - test_cache_off_path_does_not_touch_access_counters

    Schema / round-trip
        - test_artifact_round_trip_preserves_cache_fields
        - test_artifact_from_legacy_dict_without_cache_fields
        - test_artifact_defaults_access_count_zero_and_timestamp_none

    EvictionPolicy abstraction
        - test_eviction_policy_is_abstract
        - test_lru_policy_returns_rightmost_entry
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from artifacts import (
    AdmissionDecision,
    Artifact,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
    ArtifactType,
    LRUEvictionPolicy,
    SemanticLRUCache,
    SemanticLRUEvictionPolicy,
)
from artifacts.cache import CacheKey


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def store_off(tmp_path: Path) -> ArtifactStore:
    """Default store: cache off. Mirrors pre-Day-14 behavior."""
    base = tmp_path / "artifacts_off"
    s = ArtifactStore(base_dir=str(base))
    yield s
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def store_on(tmp_path: Path) -> ArtifactStore:
    """Store with cache enabled and tight budgets for eviction tests."""
    base = tmp_path / "artifacts_on"
    s = ArtifactStore(
        base_dir=str(base),
        enable_cache=True,
        cache_max_entries=3,
        cache_max_bytes=10 * 1024 * 1024,        # 10 MB
        cache_max_artifact_bytes=1 * 1024 * 1024,  # 1 MB
    )
    yield s
    shutil.rmtree(base, ignore_errors=True)


def _mk(
    artifact_id: str,
    run_id: str = "run_001",
    artifact_type: ArtifactType = ArtifactType.FIELD,
    version: int = 1,
    value: int = 100,
    **overrides,
) -> Artifact:
    defaults: dict = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "producer_task": "extract_fields",
        "run_id": run_id,
        "version": version,
        "value": value,
    }
    defaults.update(overrides)
    return Artifact(**defaults)


def _key(run: str, aid: str, v: int = 1) -> CacheKey:
    return (run, aid, v)


# --------------------------------------------------------------------------- #
# R1: LRU baseline
# --------------------------------------------------------------------------- #

def test_lru_evicts_least_recently_used() -> None:
    """R1 baseline: with policy=LRU and no degree provider, the oldest
    untouched entry is the victim."""
    cache = SemanticLRUCache(
        max_entries=2, max_bytes=10 * 1024 * 1024, max_artifact_bytes=1 * 1024 * 1024,
        policy=LRUEvictionPolicy(),
    )
    a1 = _mk("a1", run_id="r")
    a2 = _mk("a2", run_id="r")
    a3 = _mk("a3", run_id="r")
    assert cache.put(a1)
    assert cache.put(a2)
    # Both present
    assert cache.contains(_key("r", "a1"))
    assert cache.contains(_key("r", "a2"))
    # Insert a3 -> a1 (LRU) is evicted
    assert cache.put(a3)
    assert not cache.contains(_key("r", "a1"))
    assert cache.contains(_key("r", "a2"))
    assert cache.contains(_key("r", "a3"))


def test_lru_get_promotes_to_most_recent() -> None:
    cache = SemanticLRUCache(
        max_entries=2, max_bytes=10 * 1024 * 1024, max_artifact_bytes=1 * 1024 * 1024,
    )
    cache.put(_mk("a1", run_id="r"))
    cache.put(_mk("a2", run_id="r"))
    # Touch a1 -> it becomes MRU; a2 is now LRU
    assert cache.get(_key("r", "a1")) is not None
    cache.put(_mk("a3", run_id="r"))
    assert cache.contains(_key("r", "a1"))
    assert not cache.contains(_key("r", "a2"))
    assert cache.contains(_key("r", "a3"))


def test_lru_empty_cache_no_victim() -> None:
    cache = SemanticLRUCache(
        max_entries=2, max_bytes=10 * 1024 * 1024, max_artifact_bytes=1 * 1024 * 1024,
    )
    # Inserting into a cache that already has 1 entry and budget for 1
    # more shouldn't try to evict (cache not full).
    assert cache.put(_mk("a1", run_id="r"))
    assert cache.contains(_key("r", "a1"))


def test_lru_max_entries_limit() -> None:
    cache = SemanticLRUCache(
        max_entries=3, max_bytes=100 * 1024 * 1024, max_artifact_bytes=10 * 1024 * 1024,
    )
    for i in range(5):
        cache.put(_mk(f"a{i}", run_id="r"))
    # Only the last 3 should remain
    assert len(cache) == 3
    assert not cache.contains(_key("r", "a0"))
    assert not cache.contains(_key("r", "a1"))
    assert cache.contains(_key("r", "a2"))
    assert cache.contains(_key("r", "a3"))
    assert cache.contains(_key("r", "a4"))


def test_lru_max_bytes_eviction() -> None:
    """Cache evicts to honor the bytes budget, not just the entry count.

    Each small Artifact is ~457 bytes JSON; 3 of them exceed 1000 bytes
    so the third insertion must trigger an eviction.
    """
    cache = SemanticLRUCache(
        max_entries=100,
        max_bytes=1000,           # fits ~2 small artifacts
        max_artifact_bytes=10_000,
    )
    cache.put(_mk("a1", run_id="r"))
    cache.put(_mk("a2", run_id="r"))
    cache.put(_mk("a3", run_id="r"))
    # Should have evicted a1 (the LRU)
    assert not cache.contains(_key("r", "a1"))
    assert cache.contains(_key("r", "a2"))
    assert cache.contains(_key("r", "a3"))


# --------------------------------------------------------------------------- #
# R2: Centrality weighting
# --------------------------------------------------------------------------- #

def test_semantic_policy_protects_high_degree_node() -> None:
    """A high-degree (provenance-central) node should be preferred over
    a low-degree node, even if the low-degree node is more recent.

    Setup:
      - max_entries = 2
      - node A: degree=100, currently MRU
      - node B: degree=0,   currently LRU
      - insert C: degree=0
      - expected victim: B (low degree, low score), not A
    """
    degrees = {
        _key("r", "A"): 100,
        _key("r", "B"): 0,
        _key("r", "C"): 0,
    }
    cache = SemanticLRUCache(
        max_entries=2,
        max_bytes=10 * 1024 * 1024,
        max_artifact_bytes=1 * 1024 * 1024,
        policy=SemanticLRUEvictionPolicy(alpha=4.0),
        degree_provider=lambda k: degrees.get(k, 0),
    )
    cache.put(_mk("A", run_id="r"))  # MRU
    cache.put(_mk("B", run_id="r"))  # LRU
    # Insert C -> should evict B (lowest score), keep A
    cache.put(_mk("C", run_id="r"))
    assert cache.contains(_key("r", "A")), "high-degree A should be protected"
    assert not cache.contains(_key("r", "B")), "low-degree B should be evicted"
    assert cache.contains(_key("r", "C"))


def test_semantic_policy_alpha_zero_equals_lru() -> None:
    """alpha=0 -> score = recency only -> identical to LRU."""
    cache = SemanticLRUCache(
        max_entries=2,
        max_bytes=10 * 1024 * 1024,
        max_artifact_bytes=1 * 1024 * 1024,
        policy=SemanticLRUEvictionPolicy(alpha=0.0),
        degree_provider=lambda k: 999,  # would dominate if alpha were > 0
    )
    cache.put(_mk("a1", run_id="r"))
    cache.put(_mk("a2", run_id="r"))
    cache.put(_mk("a3", run_id="r"))
    assert not cache.contains(_key("r", "a1"))  # LRU
    assert cache.contains(_key("r", "a2"))


def test_semantic_policy_no_degree_provider_uses_zero() -> None:
    """If no degree provider is supplied, every node is degree 0 ->
    semantic policy degrades to LRU."""
    cache = SemanticLRUCache(
        max_entries=2,
        max_bytes=10 * 1024 * 1024,
        max_artifact_bytes=1 * 1024 * 1024,
        policy=SemanticLRUEvictionPolicy(alpha=4.0),
        degree_provider=None,  # default
    )
    cache.put(_mk("a1", run_id="r"))
    cache.put(_mk("a2", run_id="r"))
    cache.put(_mk("a3", run_id="r"))
    assert not cache.contains(_key("r", "a1"))  # behaves as LRU


def test_semantic_policy_higher_degree_higher_protection() -> None:
    """Three nodes, varying degrees; the high-degree node survives
    while the low-degree one is evicted.

    With max_entries=3 and degree-ordered protection:
      high(50) > mid(10) > low(0)
    Inserting a 4th node forces exactly one eviction; the lowest-
    scored node among the three existing is `low` (degree 0, LRU),
    so it gets dropped, the new node is admitted, and `high` and
    `mid` are protected by their centrality.
    """
    degrees = {
        _key("r", "high"): 50,
        _key("r", "mid"):  10,
        _key("r", "low"):   0,
        _key("r", "new"): 100,   # high degree on the incoming
    }
    cache = SemanticLRUCache(
        max_entries=3,
        max_bytes=10 * 1024 * 1024,
        max_artifact_bytes=1 * 1024 * 1024,
        policy=SemanticLRUEvictionPolicy(alpha=1.0),
        degree_provider=lambda k: degrees.get(k, 0),
    )
    cache.put(_mk("high", run_id="r"))
    cache.put(_mk("mid",  run_id="r"))
    cache.put(_mk("low",  run_id="r"))
    cache.put(_mk("new",  run_id="r"))
    assert cache.contains(_key("r", "high"))
    assert cache.contains(_key("r", "mid"))
    assert cache.contains(_key("r", "new"))
    assert not cache.contains(_key("r", "low"))


# --------------------------------------------------------------------------- #
# R3: Failed (INVALIDATED) artifacts are never cached
# --------------------------------------------------------------------------- #

def test_invalidated_artifact_not_admitted() -> None:
    """R3: trying to put an INVALIDATED artifact returns False; it is
    not stored in the cache."""
    cache = SemanticLRUCache(
        max_entries=10, max_bytes=10 * 1024 * 1024, max_artifact_bytes=1 * 1024 * 1024,
    )
    failed = _mk("a1", run_id="r", validation_status=ArtifactStatus.INVALIDATED)
    assert not cache.put(failed)
    assert not cache.contains(_key("r", "a1"))


def test_update_status_to_invalidated_evicts_from_cache() -> None:
    """Store-level: marking a cached artifact INVALIDATED removes it
    from the cache immediately."""
    a = _mk("a1", run_id="r")
    s = ArtifactStore(
        base_dir=str(Path("./_s_invalid")),
        enable_cache=True,
    )
    s.save(a)
    assert s.cache.contains(_key("r", "a1"))
    s.update_status("r", "a1", ArtifactStatus.INVALIDATED)
    assert not s.cache.contains(_key("r", "a1"))
    shutil.rmtree("./_s_invalid", ignore_errors=True)


def test_update_status_to_valid_readmits_to_cache() -> None:
    """A status flip back to VALID should put the artifact back in cache."""
    s = ArtifactStore(
        base_dir=str(Path("./_s_readmit")),
        enable_cache=True,
    )
    a = _mk("a1", run_id="r")
    s.save(a)
    s.update_status("r", "a1", ArtifactStatus.INVALIDATED)
    assert not s.cache.contains(_key("r", "a1"))
    s.update_status("r", "a1", ArtifactStatus.VALID)
    assert s.cache.contains(_key("r", "a1"))
    shutil.rmtree("./_s_readmit", ignore_errors=True)


def test_check_admission_rejects_failed_artifact() -> None:
    cache = SemanticLRUCache(
        max_entries=10, max_bytes=10 * 1024 * 1024, max_artifact_bytes=1 * 1024 * 1024,
    )
    failed = _mk("x", run_id="r", validation_status=ArtifactStatus.INVALIDATED)
    d = cache.check_admission(failed)
    assert isinstance(d, AdmissionDecision)
    assert not d.admitted
    assert "R3" in d.reason


def test_check_admission_accepts_recovered_artifact() -> None:
    """RECOVERED status is NOT a failure status — it should be admitted."""
    cache = SemanticLRUCache(
        max_entries=10, max_bytes=10 * 1024 * 1024, max_artifact_bytes=1 * 1024 * 1024,
    )
    rec = _mk("x", run_id="r", validation_status=ArtifactStatus.RECOVERED)
    d = cache.check_admission(rec)
    assert d.admitted


# --------------------------------------------------------------------------- #
# R4: Per-artifact size cap
# --------------------------------------------------------------------------- #

def test_oversized_artifact_not_admitted() -> None:
    """R4: an artifact whose JSON size exceeds max_artifact_bytes is
    rejected at admission, even if the cache is otherwise empty."""
    cache = SemanticLRUCache(
        max_entries=10,
        max_bytes=10 * 1024 * 1024,
        max_artifact_bytes=500,  # very tight
    )
    big = _mk("big", run_id="r", content="x" * 5000)
    d = cache.check_admission(big)
    assert not d.admitted
    assert "R4" in d.reason
    assert not cache.put(big)
    assert not cache.contains(_key("r", "big"))


def test_max_artifact_bytes_zero_rejected() -> None:
    """Constructor must reject non-positive budgets."""
    with pytest.raises(ValueError):
        SemanticLRUCache(max_entries=10, max_bytes=10_000, max_artifact_bytes=0)
    with pytest.raises(ValueError):
        SemanticLRUCache(max_entries=0, max_bytes=10_000, max_artifact_bytes=100)


def test_artifact_larger_than_total_cache_rejected() -> None:
    """If a single artifact is bigger than the whole cache budget, it
    must be rejected, not silently evict everything."""
    cache = SemanticLRUCache(
        max_entries=10,
        max_bytes=2000,           # tiny total budget
        max_artifact_bytes=10_000,
    )
    huge = _mk("huge", run_id="r", content="y" * 5000)
    assert not cache.put(huge)
    assert len(cache) == 0


# --------------------------------------------------------------------------- #
# Store integration
# --------------------------------------------------------------------------- #

def test_default_cache_off_unchanged_behavior(store_off: ArtifactStore) -> None:
    """With cache off (the default), store exposes no cache object and
    existing fields behave exactly as before."""
    assert store_off.cache is None
    a = _mk("a1")
    store_off.save(a)
    got = store_off.get("run_001", "a1")
    assert got is not None
    # access_count is the new field; it should remain at default
    assert got.access_count == 0
    assert got.last_access_at is None


def test_enable_cache_admits_on_save(store_on: ArtifactStore) -> None:
    a = _mk("a1")
    store_on.save(a)
    assert store_on.cache is not None
    assert store_on.cache.contains(_key("run_001", "a1"))


def test_enable_cache_serves_from_cache_on_get(store_on: ArtifactStore) -> None:
    a = _mk("a1")
    store_on.save(a)
    # Drop the cached entry, then re-fetch via store.get
    store_on.cache.invalidate(_key("run_001", "a1"))
    assert not store_on.cache.contains(_key("run_001", "a1"))
    g = store_on.get("run_001", "a1")
    assert g is not None
    # The get() should have re-warmed the cache
    assert store_on.cache.contains(_key("run_001", "a1"))


def test_enable_cache_evicts_under_pressure(store_on: ArtifactStore) -> None:
    """With max_entries=3, saving 4 artifacts evicts the LRU one."""
    for i in range(4):
        store_on.save(_mk(f"a{i}"))
    assert store_on.cache is not None
    # a0 is the LRU; should be gone.
    assert not store_on.cache.contains(_key("run_001", "a0"))
    for i in (1, 2, 3):
        assert store_on.cache.contains(_key("run_001", f"a{i}"))


def test_reload_clears_cache(store_on: ArtifactStore) -> None:
    a = _mk("a1")
    store_on.save(a)
    assert store_on.cache.contains(_key("run_001", "a1"))
    store_on.reload()
    # After reload, in-memory index is rebuilt from disk but cache
    # is reset (the cached references may be stale).
    assert not store_on.cache.contains(_key("run_001", "a1"))


def test_cache_off_path_does_not_touch_access_counters(
    store_off: ArtifactStore,
) -> None:
    """With cache off, repeated get() must not mutate access_count or
    last_access_at — this is the 'bit-for-bit backward compat' guarantee.
    """
    a = _mk("a1")
    store_off.save(a)
    for _ in range(5):
        g = store_off.get("run_001", "a1")
        assert g is not None
        assert g.access_count == 0
        assert g.last_access_at is None


# --------------------------------------------------------------------------- #
# Schema / round-trip
# --------------------------------------------------------------------------- #

def test_artifact_round_trip_preserves_cache_fields() -> None:
    """to_dict() -> from_dict() preserves access_count and last_access_at."""
    a = Artifact(
        artifact_id="x",
        artifact_type=ArtifactType.METRIC,
        producer_task="t",
        run_id="r",
        value=1.5,
        access_count=7,
        last_access_at="2026-09-11T10:00:00+00:00",
    )
    rt = Artifact.from_dict(a.to_dict())
    assert rt.access_count == 7
    assert rt.last_access_at == "2026-09-11T10:00:00+00:00"
    # other fields preserved too
    assert rt.value == 1.5
    assert rt.artifact_id == "x"


def test_artifact_from_legacy_dict_without_cache_fields() -> None:
    """Old on-disk JSON files (written before Day 14) lack the cache
    fields. from_dict() must still load them with the defaults."""
    legacy = {
        "artifact_id": "x",
        "artifact_type": "Field",
        "producer_task": "t",
        "run_id": "r",
        "version": 1,
        "value": 42,
        "input_artifact_ids": [],
        "evidence_ids": [],
        "validation_status": "VALID",
        "confidence": None,
        "timestamp": "2026-08-18T19:48:00+00:00",
        "execution_cost": {
            "tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "monetary_usd": 0.0,
            "latency_ms": 0.0,
            "llm_calls": 0,
        },
        "meta": {},
    }
    a = Artifact.from_dict(legacy)
    assert a.access_count == 0
    assert a.last_access_at is None
    assert a.value == 42


def test_artifact_defaults_access_count_zero_and_timestamp_none() -> None:
    a = _mk("x")
    assert a.access_count == 0
    assert a.last_access_at is None


# --------------------------------------------------------------------------- #
# EvictionPolicy abstraction
# --------------------------------------------------------------------------- #

def test_eviction_policy_is_abstract() -> None:
    """EvictionPolicy cannot be instantiated directly; subclasses must
    implement select_victim."""
    from artifacts.cache import EvictionPolicy
    with pytest.raises(TypeError):
        EvictionPolicy()  # type: ignore[abstract]


def test_lru_policy_returns_rightmost_entry() -> None:
    from collections import OrderedDict
    policy = LRUEvictionPolicy()
    od: "OrderedDict[CacheKey, int]" = OrderedDict()
    od[_key("r", "a", 1)] = 1
    od[_key("r", "b", 1)] = 1
    od[_key("r", "c", 1)] = 1
    # rightmost = "c" (LRU)
    assert policy.select_victim(od, lambda _k: 0) == _key("r", "c", 1)


# --------------------------------------------------------------------------- #
# Concurrency smoke
# --------------------------------------------------------------------------- #

def test_concurrent_get_and_save(store_on: ArtifactStore) -> None:
    """Many threads save + get simultaneously; the cache must not blow
    up and the access counter on the returned artifact must be a
    positive integer."""
    a = _mk("a1")
    store_on.save(a)
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(20):
                g = store_on.get("run_001", "a1")
                assert g is not None
                assert g.access_count >= 1
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def writer() -> None:
        try:
            for i in range(10):
                store_on.save(_mk("a1", version=i + 2))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"thread errors: {errors}"
