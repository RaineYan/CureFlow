"""
Contamination propagation — Day 7 (deterministic BFS, INPUT vs EVIDENCE policy).

Policy:
  - Tool task (task_kind="tool"): a downstream is CONTAMINATED iff
    ANY of its INPUT upstream is INVALIDATED or CONTAMINATED.
  - LLM task (task_kind="llm"): a downstream Claim is CONTAMINATED
    iff ANY of its EVIDENCE upstream is INVALIDATED or CONTAMINATED.
    INPUT-only dependencies do NOT cause semantic contamination.

Other rules:
  - INVALIDATED is a higher-priority state than CONTAMINATED. We
    never overwrite INVALIDATED -> CONTAMINATED.
  - Strict version-aware: we walk the versioned ArtifactGraph, so
    contaminating field@v2 only marks downstream v2 nodes, never v1.
  - Direction is upstream -> downstream only. Never reverse.

Algorithm: BFS. Seeds = union(handed-in refs, store-state=INVALIDATED).
"""
from __future__ import annotations

from typing import Iterable, List, Set

from pydantic import BaseModel, ConfigDict

from artifacts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
)
from provenance.graph import ArtifactGraph, EdgeType


class PropagationEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: ArtifactRef
    target: ArtifactRef
    via: EdgeType


class ContaminationResult(BaseModel):
    """Outcome of one propagation pass."""
    model_config = ConfigDict(extra="forbid")

    run_id: str
    invalidated: List[ArtifactRef]      # seeds (sorted, deduped)
    contaminated: List[ArtifactRef]    # newly marked
    unaffected: List[ArtifactRef]      # all graph nodes - invalidated - contaminated
    propagation_edges: List[PropagationEdge]


def _relevant_edge_types(artifact_meta: dict) -> Set[EdgeType]:
    """Pick which edge type(s) carry contamination for a downstream.

    Tool tasks propagate via INPUT. LLM tasks propagate via EVIDENCE.
    """
    task_kind = artifact_meta.get("task_kind", "tool")
    if task_kind == "llm":
        return {EdgeType.EVIDENCE}
    # default / tool / unknown -> INPUT
    return {EdgeType.INPUT}


def _ref_key(*args) -> tuple:
    """Accept either an ArtifactRef or (run_id, artifact_id, version)."""
    if len(args) == 1 and isinstance(args[0], ArtifactRef):
        r = args[0]
        return (r.run_id, r.artifact_id, r.version)
    if len(args) == 3:
        return (args[0], args[1], args[2])
    raise TypeError(f"_ref_key expects ArtifactRef or 3-tuple, got {args!r}")


def propagate_contamination(
    graph: ArtifactGraph,
    store: ArtifactStore,
    run_id: str,
    invalidated_refs: Iterable[ArtifactRef] = (),
) -> ContaminationResult:
    """BFS from seeds along INPUT (tool) or EVIDENCE (llm) edges.

    The seeds come from two sources, unioned:
      1. `invalidated_refs` passed in (caller-known fault sources)
      2. every artifact in the run whose validation_status is INVALIDATED
         (e.g. from detect_field_faults earlier in the pipeline)
    """
    # 0. Reload the store's in-memory cache from disk so the
    #    seed scan sees the latest validation_status (e.g.
    #    `inject_field_value_tamper` may have written v1
    #    status directly to disk without updating the in-memory
    #    _by_ref cache).  Day 13.6 FINAL follow-up.
    store.reload()

    # 1. collect seeds — Day 7 exact-ArtifactRef semantics.
    #
    # INVARIANT (Day 7, locked by tests): contamination only
    # propagates along exact-version edges in the
    # ArtifactGraph.  We do NOT retroactively seed earlier
    # content versions of the same artifact_id (e.g. when
    # `field_revenue_2025@v2` is INVALIDATED, we do NOT
    # seed `field_revenue_2025@v1` just because they share
    # artifact_id).  Each (run_id, artifact_id, version)
    # triple is a separate node in the graph; contamination
    # is independent across content versions.
    #
    # The valid faulty-snapshot pattern is: tamper creates
    # v_n+1 of the field; the scenario builder then
    # re-executes the affected metrics / claims / sections /
    # merge at v_n+1 with INPUT refs that point at the v_n+1
    # field.  The v_n+1 nodes naturally form a v_n+1 chain
    # in the graph; BFS walks that chain.  v_n baseline
    # nodes are UNAFFECTED.
    #
    # Earlier Day 13.6 FINAL follow-up introduced a
    # cross-version seed expansion that retroactively seeded
    # v_n whenever v_n+1 was INVALIDATED.  That broke the
    # version-audit invariant: contaminating v2 changed the
    # status of v1's downstream.  Reverted here.
    seeds: Set[tuple] = set()
    for r in invalidated_refs:
        if r.run_id == run_id:
            seeds.add(_ref_key(r))
    for a in store.list_all_versions_by_run(run_id):
        if a.validation_status == ArtifactStatus.INVALIDATED.value:
            seeds.add(_ref_key(a.run_id, a.artifact_id, a.version))

    # 2. BFS
    contaminated: Set[tuple] = set()
    propagation_edges: List[PropagationEdge] = []

    queue: List[tuple] = list(seeds)
    while queue:
        cur_key = queue.pop(0)
        cur_ref = _ref_from_key(cur_key)
        if cur_ref not in graph._graph:
            continue
        for d_ref in graph._graph.successors(cur_ref):
            d_key = _ref_key(d_ref)
            if d_key in contaminated or d_key in seeds:
                continue
            # Look up downstream artifact in store
            d_artifact = store.get_version(
                d_ref.run_id, d_ref.artifact_id, d_ref.version,
            )
            if d_artifact is None:
                continue
            # Check edge type between cur_ref and d_ref
            et = graph.edge_type(cur_ref, d_ref)
            if et is None:
                continue
            types = et if isinstance(et, set) else {et}
            relevant = _relevant_edge_types(d_artifact.meta)
            if not (types & relevant):
                continue  # this edge type does not carry contamination for this node
            # Only mark VALID artifacts as CONTAMINATED; never downgrade
            if d_artifact.validation_status == ArtifactStatus.VALID.value:
                contaminated.add(d_key)
                store.update_status(
                    d_ref.run_id, d_ref.artifact_id,
                    ArtifactStatus.CONTAMINATED,
                )
                via = next(iter(types & relevant))
                propagation_edges.append(PropagationEdge(
                    source=cur_ref,
                    target=d_ref,
                    via=via,
                ))
                queue.append(d_key)

    # 3. unaffected = all nodes in graph minus seeds minus contaminated
    all_nodes = graph.nodes()
    unaffected = [
        n for n in all_nodes
        if _ref_key(n) not in seeds and _ref_key(n) not in contaminated
    ]

    # Sort for determinism
    sorted_seeds = sorted([_ref_from_key(k) for k in seeds], key=lambda r: (r.artifact_id, r.version))
    sorted_cont = [_ref_from_key(k) for k in sorted(contaminated)]
    sorted_unaff = sorted(unaffected, key=lambda r: (r.artifact_id, r.version))
    sorted_edges = sorted(
        propagation_edges,
        key=lambda e: (e.source.artifact_id, e.target.artifact_id, e.via.value),
    )

    return ContaminationResult(
        run_id=run_id,
        invalidated=sorted_seeds,
        contaminated=sorted_cont,
        unaffected=sorted_unaff,
        propagation_edges=sorted_edges,
    )


def _ref_to_id(t: tuple) -> str:
    return t[1]  # artifact_id (we don't need run_id here; graph is single-run)


def _ref_from_key(k: tuple) -> ArtifactRef:
    return ArtifactRef(run_id=k[0], artifact_id=k[1], version=k[2])
