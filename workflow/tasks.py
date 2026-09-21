"""
Workflow tasks — Day 2 (revised to use Day 1's data contract).

This module is the FIRST real consumer of the Artifact data contract.
Everything here is deterministic; no LLM, no network, no randomness.

Two responsibilities:
  1. extract_fields()       - CSV -> RawDataArtifact + FieldArtifacts
  2. compute_*()            - FieldArtifacts -> MetricArtifacts

Conventions baked in (Day 4 graph / Day 7 detector will rely on these):

  - RawDataArtifact
        artifact_id = "raw_data"
        value       = list of dict records (one per CSV row)
        meta        = { csv_path, row_count, columns }
  - FieldArtifact naming
        field_<column>_<year>     e.g. field_revenue_2025
        value   = float annual sum
        input_artifact_ids = [ArtifactRef(raw_data, v1)]
  - MetricArtifact naming
        metric_<name>             e.g. metric_revenue_growth
        value   = float (a ratio)
        input_artifact_ids = [ArtifactRef(field_x, v1), ...]   (exact refs)

No decorator yet (Day 3 will automate this once we have the pattern locked
down with these hand-written refs).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List

import pandas as pd

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType


# =====================================================================
# Constants
# =====================================================================

RAW_DATA_ARTIFACT_ID = "raw_data"
RAW_DATA_VERSION = 1
FIELD_VERSION = 1
METRIC_VERSION = 1
MAX_CSV_BYTES = 5 * 1024 * 1024

# Per-column aggregation policy.
#
# users  -> mean    (column is monthly active users; Field = annual average MAU)
# others -> sum     (revenue/cost/orders/visits are flow quantities)
#
# `semantic` is recorded in the FieldArtifact.meta so Day 7/8/9 can reason
# about what the number means, not just how it was computed.
YEAR_COLUMNS: List[dict] = [
    {"column": "revenue", "year": 2024, "aggregation": "sum",  "semantic": "annual_revenue"},
    {"column": "revenue", "year": 2025, "aggregation": "sum",  "semantic": "annual_revenue"},
    {"column": "cost",    "year": 2024, "aggregation": "sum",  "semantic": "annual_cost"},
    {"column": "cost",    "year": 2025, "aggregation": "sum",  "semantic": "annual_cost"},
    {"column": "users",   "year": 2024, "aggregation": "mean", "semantic": "monthly_active_users"},
    {"column": "users",   "year": 2025, "aggregation": "mean", "semantic": "monthly_active_users"},
    {"column": "orders",  "year": 2024, "aggregation": "sum",  "semantic": "annual_orders"},
    {"column": "orders",  "year": 2025, "aggregation": "sum",  "semantic": "annual_orders"},
    {"column": "visits",  "year": 2024, "aggregation": "sum",  "semantic": "annual_visits"},
    {"column": "visits",  "year": 2025, "aggregation": "sum",  "semantic": "annual_visits"},
]


# =====================================================================
# Helpers
# =====================================================================

def _field_id(column: str, year: int) -> str:
    return f"field_{column}_{year}"


def _ref(run_id: str, artifact_id: str, version: int = 1) -> ArtifactRef:
    return ArtifactRef(run_id=run_id, artifact_id=artifact_id, version=version)


def _get_field(store: ArtifactStore, run_id: str, column: str, year: int) -> float:
    """Pull a FieldArtifact's value. Raises KeyError if missing."""
    aid = _field_id(column, year)
    a = store.get(run_id, aid)
    if a is None:
        raise KeyError(f"FieldArtifact not found: ({run_id}, {aid})")
    return float(a.value)


def _safe_ratio(numerator: float, denominator: float, metric_name: str) -> float:
    """Compute a finite ratio with a stable, client-readable zero boundary."""
    if denominator == 0:
        raise ValueError(f"{metric_name} denominator cannot be zero")
    value = numerator / denominator
    if not math.isfinite(value):
        raise ValueError(f"{metric_name} result must be finite")
    return value


def _read_business_csv(csv_path: str) -> pd.DataFrame:
    path = Path(csv_path)
    if path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError("CSV file cannot exceed 5 MB")
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            df = pd.read_csv(path, encoding="gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV encoding must be UTF-8 or GBK/GB18030") from exc
    except pd.errors.EmptyDataError as exc:
        raise ValueError("CSV must contain a header and data rows") from exc

    if df.empty:
        raise ValueError("CSV must contain at least one data row")
    required = {"year", *(spec["column"] for spec in YEAR_COLUMNS)}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"CSV missing required columns: {', '.join(missing)}")
    for column in sorted(required):
        try:
            df[column] = pd.to_numeric(df[column], errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CSV column {column!r} must contain numeric values") from exc
        if not all(math.isfinite(float(value)) for value in df[column]):
            raise ValueError(f"CSV column {column!r} must contain finite values")
    years = {int(value) for value in df["year"]}
    if not {2024, 2025}.issubset(years):
        raise ValueError("CSV must contain data rows for both 2024 and 2025")
    return df


# =====================================================================
# Field extraction
# =====================================================================

def extract_fields(
    csv_path: str,
    run_id: str,
    store: ArtifactStore,
) -> Dict[str, Artifact]:
    """Read CSV and emit one RawDataArtifact + one FieldArtifact per (column, year).

    Returns a dict {field_id: Artifact} for caller convenience; the same
    artifacts are also persisted in the store.

    Field naming:    field_<column>_<year>      e.g. field_revenue_2025
    Field value:     float annual sum
    Field refs:      [ArtifactRef(raw_data, v1)]
    """
    df = _read_business_csv(csv_path)

    raw = Artifact(
        artifact_id=RAW_DATA_ARTIFACT_ID,
        artifact_type=ArtifactType.RAW_DATA,
        producer_task="extract_fields",
        run_id=run_id,
        version=RAW_DATA_VERSION,
        value=df.to_dict(orient="records"),
        meta={
            "csv_path": csv_path,
            "row_count": int(len(df)),
            "columns": list(df.columns),
        },
    )
    store.save(raw)

    raw_ref = _ref(run_id, RAW_DATA_ARTIFACT_ID, RAW_DATA_VERSION)

    out: Dict[str, Artifact] = {}
    for spec in YEAR_COLUMNS:
        column = spec["column"]
        year = spec["year"]
        aggregation = spec["aggregation"]
        semantic = spec["semantic"]

        if column not in df.columns:
            raise KeyError(f"Column '{column}' not in CSV {csv_path}")

        year_df = df.loc[df["year"] == year, column]
        if aggregation == "sum":
            value = float(year_df.sum())
        elif aggregation == "mean":
            value = float(year_df.mean())
        else:
            raise ValueError(f"Unknown aggregation: {aggregation!r}")

        a = Artifact(
            artifact_id=_field_id(column, year),
            artifact_type=ArtifactType.FIELD,
            producer_task="extract_fields",
            run_id=run_id,
            version=FIELD_VERSION,
            value=value,
            input_artifact_ids=[raw_ref],
            meta={
                "column": column,
                "year": year,
                "aggregation": aggregation,
                "semantic": semantic,
            },
        )
        store.save(a)
        out[a.artifact_id] = a

    return out


# =====================================================================
# Ground truth recomputation (used by Day 7 detector; not used by Day 2 itself)
# =====================================================================

def recompute_field_from_raw(
    raw_artifact: Artifact,
    field_name: str,
    aggregation: str = "sum",
) -> float:
    """Recompute a FieldArtifact's value from a RawDataArtifact.

    Used by the future detector: if someone tampered with field_revenue_2025
    in the store, we can re-derive the correct value by reading raw_data's
    records and aggregating.

    field_name format: "field_<column>_<year>"
        e.g. "field_revenue_2025" -> column="revenue", year=2025

    aggregation: "sum" (default, used by revenue/cost/orders/visits) or
                 "mean" (used by users MAU). The validator passes the
                 aggregation from the field's meta so this re-derivation
                 matches how the field was originally produced.

    Raises ValueError on bad field_name; KeyError on unknown column/year.
    """
    if not field_name.startswith("field_"):
        raise ValueError(f"Bad field_name (must start with 'field_'): {field_name!r}")
    rest = field_name[len("field_"):]
    if len(rest) < 5 or not rest[-4:].isdigit():
        raise ValueError(f"Bad field_name (missing year suffix): {field_name!r}")
    year = int(rest[-4:])
    column = rest[:-5]

    df = pd.DataFrame(raw_artifact.value)
    if column not in df.columns:
        raise KeyError(f"Column '{column}' not in raw_data records")
    year_df = df.loc[df["year"] == year, column]
    if aggregation == "sum":
        return float(year_df.sum())
    if aggregation == "mean":
        return float(year_df.mean())
    raise ValueError(f"Unknown aggregation: {aggregation!r}")


# =====================================================================
# Metric tools — one function per metric, all deterministic
# =====================================================================
#
# Every metric function:
#   1. reads its FieldArtifact inputs via store.get(run_id, ...)
#   2. computes the formula
#   3. saves a MetricArtifact with input_artifact_ids = [ArtifactRef(...)]
#
# The function signatures and the input-ref lists are intentionally explicit;
# Day 3 will replace the manual ref wiring with @track_provenance once we
# have a few metrics to verify the decorator is correct.
# =====================================================================

def compute_revenue_growth(run_id: str, store: ArtifactStore) -> Artifact:
    """revenue_growth = (revenue_2025 - revenue_2024) / revenue_2024"""
    rev25 = _get_field(store, run_id, "revenue", 2025)
    rev24 = _get_field(store, run_id, "revenue", 2024)
    value = _safe_ratio(rev25 - rev24, rev24, "revenue_growth")
    return store.save(Artifact(
        artifact_id="metric_revenue_growth",
        artifact_type=ArtifactType.METRIC,
        producer_task="compute_revenue_growth",
        run_id=run_id,
        version=METRIC_VERSION,
        value=value,
        input_artifact_ids=[
            _ref(run_id, _field_id("revenue", 2025)),
            _ref(run_id, _field_id("revenue", 2024)),
        ],
    ))


def compute_profit_margin(run_id: str, store: ArtifactStore) -> Artifact:
    """profit_margin = (revenue_2025 - cost_2025) / revenue_2025"""
    rev25 = _get_field(store, run_id, "revenue", 2025)
    cost25 = _get_field(store, run_id, "cost", 2025)
    value = _safe_ratio(rev25 - cost25, rev25, "profit_margin")
    return store.save(Artifact(
        artifact_id="metric_profit_margin",
        artifact_type=ArtifactType.METRIC,
        producer_task="compute_profit_margin",
        run_id=run_id,
        version=METRIC_VERSION,
        value=value,
        input_artifact_ids=[
            _ref(run_id, _field_id("revenue", 2025)),
            _ref(run_id, _field_id("cost", 2025)),
        ],
    ))


def compute_cost_ratio(run_id: str, store: ArtifactStore) -> Artifact:
    """cost_ratio = cost_2025 / revenue_2025"""
    cost25 = _get_field(store, run_id, "cost", 2025)
    rev25 = _get_field(store, run_id, "revenue", 2025)
    value = _safe_ratio(cost25, rev25, "cost_ratio")
    return store.save(Artifact(
        artifact_id="metric_cost_ratio",
        artifact_type=ArtifactType.METRIC,
        producer_task="compute_cost_ratio",
        run_id=run_id,
        version=METRIC_VERSION,
        value=value,
        input_artifact_ids=[
            _ref(run_id, _field_id("cost", 2025)),
            _ref(run_id, _field_id("revenue", 2025)),
        ],
    ))


def compute_user_growth(run_id: str, store: ArtifactStore) -> Artifact:
    """user_growth = (users_2025 - users_2024) / users_2024"""
    u25 = _get_field(store, run_id, "users", 2025)
    u24 = _get_field(store, run_id, "users", 2024)
    value = _safe_ratio(u25 - u24, u24, "user_growth")
    return store.save(Artifact(
        artifact_id="metric_user_growth",
        artifact_type=ArtifactType.METRIC,
        producer_task="compute_user_growth",
        run_id=run_id,
        version=METRIC_VERSION,
        value=value,
        input_artifact_ids=[
            _ref(run_id, _field_id("users", 2025)),
            _ref(run_id, _field_id("users", 2024)),
        ],
    ))


def compute_conversion_rate(run_id: str, store: ArtifactStore) -> Artifact:
    """conversion_rate = orders_2025 / visits_2025"""
    o25 = _get_field(store, run_id, "orders", 2025)
    v25 = _get_field(store, run_id, "visits", 2025)
    value = _safe_ratio(o25, v25, "conversion_rate")
    return store.save(Artifact(
        artifact_id="metric_conversion_rate",
        artifact_type=ArtifactType.METRIC,
        producer_task="compute_conversion_rate",
        run_id=run_id,
        version=METRIC_VERSION,
        value=value,
        input_artifact_ids=[
            _ref(run_id, _field_id("orders", 2025)),
            _ref(run_id, _field_id("visits", 2025)),
        ],
    ))


def compute_order_growth(run_id: str, store: ArtifactStore) -> Artifact:
    """order_growth = (orders_2025 - orders_2024) / orders_2024"""
    o25 = _get_field(store, run_id, "orders", 2025)
    o24 = _get_field(store, run_id, "orders", 2024)
    value = _safe_ratio(o25 - o24, o24, "order_growth")
    return store.save(Artifact(
        artifact_id="metric_order_growth",
        artifact_type=ArtifactType.METRIC,
        producer_task="compute_order_growth",
        run_id=run_id,
        version=METRIC_VERSION,
        value=value,
        input_artifact_ids=[
            _ref(run_id, _field_id("orders", 2025)),
            _ref(run_id, _field_id("orders", 2024)),
        ],
    ))
