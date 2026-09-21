"""
Recompute handlers — Day 8.

Pure-Python recovery functions for deterministic Tool artifacts.
Each one:
  1. Reads the bad artifact (or its declared inputs).
  2. Calls the recomputation function (deterministic).
  3. Saves a new content version (next_v = current_v + 1).
  4. Marks the new version as RECOVERED.

For the LLM tasks (claim / section) the executor in
recovery/executor.py calls the existing generate_*_claim /
generate_*_section directly with remapped input refs.
"""
from __future__ import annotations

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
    ArtifactType,
)
from provenance.decorator import RunContext, track_provenance
from workflow.tasks import recompute_field_from_raw


# =====================================================================
# Field recovery: re-derive from raw_data, save as new content version
# =====================================================================

def recompute_field_artifact_from_raw(
    bad_field: Artifact,
    raw_artifact: Artifact,
    *,
    ctx: RunContext,
) -> Artifact:
    """Re-derive a FieldArtifact's value from raw_data. New content version.

    Returns the saved Artifact (version = bad_field.version + 1).
    Marked RECOVERED. Provenance (input_artifact_ids) is preserved
    from the bad_field so downstream code can still walk the lineage.
    """
    run_id = ctx.run_id
    store = ctx.store
    aggregation = bad_field.meta.get("aggregation", "sum")
    new_value = recompute_field_from_raw(
        raw_artifact, bad_field.artifact_id, aggregation=aggregation,
    )
    next_v = bad_field.version + 1
    new_artifact = bad_field.model_copy(update={
        "value": new_value,
        "version": next_v,
        "meta": {**bad_field.meta, "recovered": True, "recovery_source": f"v{bad_field.version}"},
    })
    saved = store.save(new_artifact)
    # Status change only, no new content version implied
    store.update_status(run_id, bad_field.artifact_id, ArtifactStatus.RECOVERED)
    return saved


# =====================================================================
# Metric recovery: decorated compute functions
# =====================================================================
#
# These are decorated versions of the Day 2 compute_*_X functions.
# The decorator auto-detects the next version and captures the
# EXACT refs of the input Artifacts (post-remap).

@track_provenance(
    output_artifact_id="metric_revenue_growth",
    artifact_type=ArtifactType.METRIC,
)
def mrg_recompute(revenue_2025, revenue_2024, *, ctx):
    return (revenue_2025.value - revenue_2024.value) / revenue_2024.value


@track_provenance(
    output_artifact_id="metric_profit_margin",
    artifact_type=ArtifactType.METRIC,
)
def mpm_recompute(revenue_2025, cost_2025, *, ctx):
    return (revenue_2025.value - cost_2025.value) / revenue_2025.value


@track_provenance(
    output_artifact_id="metric_cost_ratio",
    artifact_type=ArtifactType.METRIC,
)
def mcr_recompute(cost_2025, revenue_2025, *, ctx):
    return cost_2025.value / revenue_2025.value


@track_provenance(
    output_artifact_id="metric_conversion_rate",
    artifact_type=ArtifactType.METRIC,
)
def conv_recompute(orders_2025, visits_2025, *, ctx):
    return orders_2025.value / visits_2025.value


@track_provenance(
    output_artifact_id="metric_user_growth",
    artifact_type=ArtifactType.METRIC,
)
def mug_recompute(users_2025, users_2024, *, ctx):
    return users_2025.value / users_2024.value


@track_provenance(
    output_artifact_id="metric_order_growth",
    artifact_type=ArtifactType.METRIC,
)
def mog_recompute(orders_2025, orders_2024, *, ctx):
    return orders_2025.value / orders_2024.value


# =====================================================================
# mark_recovered helper
# =====================================================================

def mark_recovered(store: ArtifactStore, run_id: str, artifact_id: str) -> None:
    """Mark the LATEST content version of an artifact as RECOVERED.

    Used after an LLM re-prompt produces a new version. We do NOT
    touch the old (bad) version's status — it stays CONTAMINATED
    in the store as part of the fault history.
    """
    latest = store.get(run_id, artifact_id)
    if latest is not None:
        store.update_status(run_id, artifact_id, ArtifactStatus.RECOVERED)
