"""
ArtifactGraph — Day 4 (final).

Version-aware provenance graph built from an ArtifactStore.

Node identity  = ArtifactRef(run_id, artifact_id, version)
Edge direction = upstream -> downstream
Edge types     = INPUT (data dependency) | EVIDENCE (semantic citation)

Invariant: every node in the graph corresponds to an Artifact that
exists in the store. Every edge comes from an Artifact's own
input_artifact_ids / evidence_ids. The graph is a *rebuildable view*
of provenance — it does not store extra information that the store
doesn't already carry.

Day 4 does NOT do contamination propagation, fault detection, recovery
planning, or any LLM work. It only answers "who depends on whom?".
"""
from __future__ import annotations

from enum import Enum
from typing import Iterator, Optional, Set, Union

import networkx as nx

from artifacts import Artifact, ArtifactRef, ArtifactStore


class EdgeType(str, Enum):
    INPUT = "INPUT"       # data dependency: this artifact consumed `source`
    EVIDENCE = "EVIDENCE" # semantic citation: this artifact cited `source`


# Multi-edge allowed: an INPUT and an EVIDENCE could coexist between
# the same (source, target) pair in principle. MultiDiGraph handles this.
class ArtifactGraph:
    """A version-aware provenance graph for a single run."""

    def __init__(self) -> None:
        # Node key = ArtifactRef (frozen, hashable, eq-comparable).
        # MultiDiGraph so we can attach {type: EdgeType} per edge.
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()

    # ---------- construction ----------

    @classmethod
    def from_store(cls, store: ArtifactStore, run_id: str) -> "ArtifactGraph":
        """Build the graph by reading the store's saved provenance.

        Includes EVERY content version (v1, v2, ...) so recovery history
        is preserved. Rejects:
          - cross-run refs (ref.run_id != run_id)
          - dangling refs (ref points to a version that doesn't exist in the run)
        """
        g = cls()
        artifacts = store.list_all_versions_by_run(run_id)

        # 1) register all nodes first so edge addition always finds them
        for a in artifacts:
            ref = ArtifactRef(
                run_id=a.run_id,
                artifact_id=a.artifact_id,
                version=a.version,
            )
            g._graph.add_node(ref, artifact=a)

        # 2) add edges from the saved provenance
        for a in artifacts:
            target = ArtifactRef(
                run_id=a.run_id, artifact_id=a.artifact_id, version=a.version,
            )
            for ref in a.input_artifact_ids:
                _validate_ref(ref, run_id, g)
                g._graph.add_edge(ref, target, type=EdgeType.INPUT)
            for ref in a.evidence_ids:
                _validate_ref(ref, run_id, g)
                g._graph.add_edge(ref, target, type=EdgeType.EVIDENCE)

        return g

    # ---------- node / edge queries ----------

    def has_node(self, ref: ArtifactRef) -> bool:
        return self._graph.has_node(ref)

    def nodes(self) -> Set[ArtifactRef]:
        return set(self._graph.nodes())

    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    def edges(self) -> Iterator[tuple[ArtifactRef, ArtifactRef, EdgeType]]:
        """Iterate every typed provenance edge without exposing networkx internals."""
        for source, target, attributes in self._graph.edges(data=True):
            edge_type = attributes.get("type")
            if isinstance(edge_type, EdgeType):
                yield source, target, edge_type

    def artifact(self, ref: ArtifactRef) -> Optional[Artifact]:
        """Return the artifact metadata attached to a node, if present."""
        if ref not in self._graph:
            return None
        return self._graph.nodes[ref].get("artifact")

    # ---------- traversal ----------

    def get_upstream(self, ref: ArtifactRef) -> Set[ArtifactRef]:
        """All direct predecessors (consumed by / cited by `ref`)."""
        if ref not in self._graph:
            return set()
        return set(self._graph.predecessors(ref))

    def get_downstream(self, ref: ArtifactRef) -> Set[ArtifactRef]:
        """All direct successors (artifacts that consumed / cited `ref`)."""
        if ref not in self._graph:
            return set()
        return set(self._graph.successors(ref))

    def successors(self, ref: ArtifactRef) -> Set[ArtifactRef]:
        """Alias for get_downstream; named for clarity in BFS code."""
        return self.get_downstream(ref)

    def get_ancestors(self, ref: ArtifactRef) -> Set[ArtifactRef]:
        """Transitive upstream closure (BFS backwards)."""
        if ref not in self._graph:
            return set()
        return set(nx.ancestors(self._graph, ref))

    def get_descendants(self, ref: ArtifactRef) -> Set[ArtifactRef]:
        """Transitive downstream closure (BFS forwards)."""
        if ref not in self._graph:
            return set()
        return set(nx.descendants(self._graph, ref))

    def has_path(self, source: ArtifactRef, target: ArtifactRef) -> bool:
        if source not in self._graph or target not in self._graph:
            return False
        return nx.has_path(self._graph, source, target)

    def edge_type(
        self,
        source: ArtifactRef,
        target: ArtifactRef,
    ) -> Union[EdgeType, Set[EdgeType], None]:
        """Return the edge type(s) between source and target.

        Returns a single EdgeType if there is exactly one type, a set of
        EdgeType values if there are multiple, or None if no edge.
        """
        if not self._graph.has_edge(source, target):
            return None
        data = self._graph.get_edge_data(source, target)
        types: Set[EdgeType] = set()
        for _key, attrs in data.items():
            if "type" in attrs:
                types.add(attrs["type"])
        if not types:
            return None
        if len(types) == 1:
            return next(iter(types))
        return types

    # ---------- debug helpers ----------

    def pretty_print(self) -> str:
        """Human-readable dump of the graph, one (src, dst) per line with
        all edge types shown.

        MultiDiGraph stores multi-edges with separate keys; we deduplicate
        (src, dst) pairs here so the dump is readable.
        """
        lines: list[str] = []
        seen: set[tuple[ArtifactRef, ArtifactRef]] = set()
        for src in sorted(self._graph.nodes(), key=lambda r: (r.artifact_id, r.version)):
            outgoing_targets = sorted(
                self._graph.successors(src), key=lambda r: (r.artifact_id, r.version)
            )
            for dst in outgoing_targets:
                if (src, dst) in seen:
                    continue
                seen.add((src, dst))
                et = self.edge_type(src, dst)
                et_str = et.value if isinstance(et, EdgeType) else "|".join(
                    sorted(e.value for e in et)
                )
                lines.append(
                    f"  {src.artifact_id}@v{src.version}"
                    f"  ->  {dst.artifact_id}@v{dst.version}  [{et_str}]"
                )
        return "\n".join(lines)


# =====================================================================
# Helpers
# =====================================================================

def _validate_ref(ref: ArtifactRef, run_id: str, g: "ArtifactGraph") -> None:
    """Reject cross-run and dangling refs at graph-build time."""
    if ref.run_id != run_id:
        raise ValueError(
            f"Cross-run ArtifactRef not allowed: graph run_id={run_id!r} "
            f"but ref.run_id={ref.run_id!r} (ref={ref.to_ref_string()})"
        )
    if not g._graph.has_node(ref):
        raise ValueError(
            f"Dangling ArtifactRef: {ref.to_ref_string()} is referenced but "
            f"not present in the store for run_id={run_id!r}"
        )
