"""Boundary, encoding, scale, numeric, and concurrent-store robustness tests."""
from __future__ import annotations

import codecs
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from artifacts import Artifact, ArtifactStore, ArtifactType
from workflow.tasks import (
    compute_conversion_rate,
    compute_cost_ratio,
    compute_order_growth,
    compute_profit_margin,
    compute_revenue_growth,
    compute_user_growth,
    extract_fields,
)


COLUMNS = "year,month,revenue,cost,users,orders,visits"


def csv_text(value_2025="20", *, extra_column=False) -> str:
    suffix = ",备注" if extra_column else ""
    return (
        COLUMNS + (",label" if extra_column else "") + "\n"
        f"2024,1,10,5,2,3,30{suffix}\n"
        f"2025,1,{value_2025},8,4,6,40{suffix}\n"
    )


def write_csv(path: Path, text: str, encoding="utf-8") -> Path:
    path.write_bytes(text.encode(encoding))
    return path


@pytest.mark.parametrize("content", ["", COLUMNS + "\n"])
def test_empty_csv_variants_are_rejected(tmp_path: Path, content: str) -> None:
    path = write_csv(tmp_path / "empty.csv", content)
    with pytest.raises(ValueError, match="header|data row"):
        extract_fields(str(path), "empty", ArtifactStore(str(tmp_path / "store")))


def test_one_row_csv_is_rejected_with_missing_year_message(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "one.csv", COLUMNS + "\n2025,1,1,1,1,1,1\n")
    with pytest.raises(ValueError, match="both 2024 and 2025"):
        extract_fields(str(path), "one", ArtifactStore(str(tmp_path / "store")))


def test_ten_thousand_row_csv_is_accepted(tmp_path: Path) -> None:
    rows = [COLUMNS]
    rows.extend(f"{2024 + index % 2},1,10,5,2,3,30" for index in range(10_000))
    path = write_csv(tmp_path / "large.csv", "\n".join(rows) + "\n")
    store = ArtifactStore(str(tmp_path / "store"))
    fields = extract_fields(str(path), "large", store)
    assert len(fields) == 10
    assert store.get("large", "raw_data").meta["row_count"] == 10_000


def test_one_hundred_mb_csv_is_rejected_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "100mb.csv"
    with path.open("wb") as handle:
        handle.truncate(100 * 1024 * 1024)
    with pytest.raises(ValueError, match="5 MB"):
        extract_fields(str(path), "huge", ArtifactStore(str(tmp_path / "store")))


@pytest.mark.parametrize("missing", ["revenue", "users", "visits"])
def test_missing_required_columns_are_rejected(tmp_path: Path, missing: str) -> None:
    columns = COLUMNS.split(",")
    index = columns.index(missing)
    kept = [column for column in columns if column != missing]
    rows = [["2024", "1", "10", "5", "2", "3", "30"], ["2025", "1", "20", "8", "4", "6", "40"]]
    text = ",".join(kept) + "\n" + "\n".join(",".join(row[:index] + row[index + 1:]) for row in rows) + "\n"
    with pytest.raises(ValueError, match=missing):
        extract_fields(str(write_csv(tmp_path / "missing.csv", text)), "missing", ArtifactStore(str(tmp_path / "store")))


@pytest.mark.parametrize("value", ["0", "-1", "0.125", "42", "+8", "1e6", "999999999999", "-0.5"])
def test_numeric_boundaries_extract_as_finite_values(tmp_path: Path, value: str) -> None:
    path = write_csv(tmp_path / "numeric.csv", csv_text(value))
    fields = extract_fields(str(path), f"numeric_{value}", ArtifactStore(str(tmp_path / "store")))
    assert fields["field_revenue_2025"].value == pytest.approx(float(value))


@pytest.mark.parametrize("value", ["oops", "1_000", "NaN", "inf", "-inf", "一百"])
def test_non_numeric_or_non_finite_values_are_rejected(tmp_path: Path, value: str) -> None:
    path = write_csv(tmp_path / "invalid.csv", csv_text(value))
    with pytest.raises(ValueError, match="numeric|finite"):
        extract_fields(str(path), "invalid", ArtifactStore(str(tmp_path / "store")))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gbk"])
def test_supported_encodings_and_chinese_text(tmp_path: Path, encoding: str) -> None:
    path = write_csv(tmp_path / f"{encoding}.csv", csv_text(extra_column=True), encoding)
    store = ArtifactStore(str(tmp_path / f"store_{encoding}"))
    extract_fields(str(path), f"run_{encoding}", store)
    raw = store.get(f"run_{encoding}", "raw_data")
    assert raw.value[0]["label"] == "备注"


@pytest.mark.parametrize("payload", [b"\xff\xfe\xff", b"\x81"])
def test_undecodable_bytes_are_rejected(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "garbled.csv"
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="encoding"):
        extract_fields(str(path), "garbled", ArtifactStore(str(tmp_path / "store")))


@pytest.mark.parametrize(
    ("metric", "zero_field"),
    [
        (compute_revenue_growth, "field_revenue_2024"),
        (compute_profit_margin, "field_revenue_2025"),
        (compute_cost_ratio, "field_revenue_2025"),
        (compute_user_growth, "field_users_2024"),
        (compute_conversion_rate, "field_visits_2025"),
        (compute_order_growth, "field_orders_2024"),
    ],
)
def test_metric_zero_denominators_are_explicit(tmp_path: Path, metric, zero_field: str) -> None:
    store = ArtifactStore(str(tmp_path / "store"))
    for column in ["revenue", "cost", "users", "orders", "visits"]:
        for year in [2024, 2025]:
            artifact_id = f"field_{column}_{year}"
            store.save(Artifact(
                artifact_id=artifact_id, artifact_type=ArtifactType.FIELD,
                producer_task="robustness", run_id="zero", version=1,
                value=0 if artifact_id == zero_field else 1,
            ))
    with pytest.raises(ValueError, match="denominator cannot be zero"):
        metric("zero", store)


def make_artifact(index: int, *, artifact_id=None, version=1) -> Artifact:
    return Artifact(
        artifact_id=artifact_id or f"concurrent_{index}", artifact_type=ArtifactType.FIELD,
        producer_task="robustness", run_id="threads", version=version, value=index,
    )


def test_ten_threads_save_distinct_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path / "store"))
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(store.save, [make_artifact(index) for index in range(10)]))
    assert len(store.list_by_run("threads")) == 10


def test_ten_threads_same_key_allow_exactly_one_save(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path / "store"))
    def save_once(index):
        try:
            store.save(make_artifact(index, artifact_id="same"))
            return "saved"
        except ValueError:
            return "duplicate"
    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(save_once, range(10)))
    assert outcomes.count("saved") == 1 and outcomes.count("duplicate") == 9


def test_ten_threads_save_versions_and_latest_is_deterministic(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path / "store"))
    artifacts = [make_artifact(version, artifact_id="versioned", version=version) for version in range(1, 11)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(store.save, artifacts))
    assert store.list_versions("threads", "versioned") == list(range(1, 11))
    assert store.get("threads", "versioned").version == 10


def test_concurrent_saved_files_reload_cleanly(tmp_path: Path) -> None:
    base = tmp_path / "store"
    store = ArtifactStore(str(base))
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(store.save, [make_artifact(index) for index in range(10)]))
    reloaded = ArtifactStore(str(base))
    reloaded.reload()
    assert sorted(reloaded.list_ids_by_run("threads")) == [f"concurrent_{index}" for index in range(10)]


def test_concurrent_gets_return_isolated_copies(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path / "store"))
    store.save(make_artifact(1, artifact_id="read"))
    with ThreadPoolExecutor(max_workers=10) as pool:
        values = list(pool.map(lambda _: store.get("threads", "read"), range(10)))
    values[0].value = 999
    assert all(item is not values[0] for item in values[1:])
    assert store.get("threads", "read").value == 1
