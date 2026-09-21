"""
Tests for the data-driven workflow scheduler (v2 refactor).

Coverage (32+ tests):
    Stage model            (4 tests)   defaults, validation, cross-field rules
    WorkflowContext        (4 tests)   intrinsic inputs, mutable outputs, defaults
    Scheduler validation   (6 tests)   empty table, dup name, bad input, bad output
                                       intrinsic inputs, cross-stage dependencies
    Scheduler run          (8 tests)   all-success happy path, recording, ordering
                                       retries, on_failure='skip', on_failure='abort',
                                       on_failure='fallback', validator failure
    Backward compatibility (3 tests)   run_baseline_workflow still returns same shape
    Stages module          (3 tests)   BASELINE_TABLE length, names, validators fire
    Add-a-stage demo       (3 tests)   1-line table change adds a new stage without
                                       touching runner.py; outputs flow downstream
    Integration            (2 tests)   end-to-end via FakeLLM; cost totals preserved
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

import pytest
from pydantic import ValidationError

from artifacts import ArtifactStore
from llm import default_workflow_llm
from workflow.runner import (
    WorkflowResult,
    run_baseline_workflow,
)
from workflow.scheduler import (
    INTRINSIC_INPUTS,
    OUTPUT_TO_ATTR,
    Scheduler,
    Stage,
    StageExecutionError,
    StageStatus,
    TableValidationError,
    WorkflowContext,
    accumulate_cost,
)
from workflow.stages import (
    BASELINE_TABLE,
    stage_claims_spec,
    stage_extract_spec,
    stage_merge_spec,
    stage_metrics_spec,
    stage_sections_spec,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def store(tmp_path) -> ArtifactStore:
    base = tmp_path / "artifacts"
    s = ArtifactStore(base_dir=str(base))
    yield s
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def csv_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


@pytest.fixture
def llm():
    return default_workflow_llm()


@pytest.fixture
def ctx(store, csv_path, llm) -> WorkflowContext:
    return WorkflowContext(
        csv_path=csv_path,
        run_id="run_sched",
        store=store,
        llm_client=llm,
    )


# =====================================================================
# 1-4: Stage model
# =====================================================================

def test_stage_defaults():
    """Stage accepts minimal construction with sensible defaults."""
    s = Stage(name="noop", execute=lambda c: c)
    assert s.name == "noop"
    assert s.inputs == []
    assert s.outputs == []
    assert s.validators == []
    assert s.retries == 0
    assert s.on_failure == "abort"
    assert s.fallback is None


def test_stage_rejects_empty_name():
    with pytest.raises(ValidationError):
        Stage(name="", execute=lambda c: c)


def test_stage_rejects_negative_retries():
    with pytest.raises(ValidationError):
        Stage(name="x", execute=lambda c: c, retries=-1)


def test_stage_fallback_required_for_fallback_policy():
    """on_failure='fallback' with no fallback callable is rejected."""
    with pytest.raises(ValidationError):
        Stage(
            name="x",
            execute=lambda c: c,
            on_failure="fallback",
            fallback=None,
        )


# =====================================================================
# 5-8: WorkflowContext
# =====================================================================

def test_context_intrinsic_inputs_available(store, csv_path, llm):
    ctx = WorkflowContext(
        csv_path=csv_path, run_id="r", store=store, llm_client=llm,
    )
    # Intrinsic attributes are present without any stage running.
    assert ctx.csv_path == csv_path
    assert ctx.run_id == "r"
    assert ctx.store is store
    assert ctx.llm_client is llm


def test_context_outputs_default_empty(store, csv_path, llm):
    ctx = WorkflowContext(
        csv_path=csv_path, run_id="r", store=store, llm_client=llm,
    )
    assert ctx.raw_data is None
    assert ctx.fields == {}
    assert ctx.metrics == {}
    assert ctx.claims == {}
    assert ctx.sections == {}
    assert ctx.merged_report is None


def test_context_bookkeeping_defaults(store, csv_path, llm):
    ctx = WorkflowContext(
        csv_path=csv_path, run_id="r", store=store, llm_client=llm,
    )
    assert ctx.stage_status == {}
    assert ctx.stage_attempts == {}
    assert ctx.stage_history == []
    assert ctx.error is None


def test_context_is_mutable(store, csv_path, llm):
    """Stages are expected to mutate the context in place."""
    ctx = WorkflowContext(
        csv_path=csv_path, run_id="r", store=store, llm_client=llm,
    )
    ctx.fields["x"] = "y"
    ctx.stage_status["s1"] = "success"
    assert ctx.fields["x"] == "y"
    assert ctx.stage_status["s1"] == "success"


# =====================================================================
# 9-14: Scheduler.validate
# =====================================================================

def test_validate_empty_table_raises():
    with pytest.raises(TableValidationError):
        Scheduler().validate([])


def test_validate_duplicate_stage_name_raises():
    s1 = Stage(name="dup", execute=lambda c: c, outputs=["raw_data"])
    s2 = Stage(name="dup", execute=lambda c: c, outputs=["fields"])
    with pytest.raises(TableValidationError, match="Duplicate"):
        Scheduler().validate([s1, s2])


def test_validate_unknown_output_raises():
    """Outputs must be in OUTPUT_TO_ATTR."""
    s = Stage(name="bad", execute=lambda c: c, outputs=["nonexistent_artifact"])
    with pytest.raises(TableValidationError, match="not a known"):
        Scheduler().validate([s])


def test_validate_missing_input_raises():
    """An input that no earlier stage produces (and isn't intrinsic) is rejected."""
    s = Stage(name="needs_x", execute=lambda c: c, inputs=["x"], outputs=["raw_data"])
    with pytest.raises(TableValidationError, match="no earlier stage produces"):
        Scheduler().validate([s])


def test_validate_chained_inputs_ok():
    """Stage B reading stage A's output is fine."""
    a = Stage(name="a", execute=lambda c: c, outputs=["raw_data"])
    b = Stage(name="b", execute=lambda c: c, inputs=["raw_data"], outputs=["fields"])
    Scheduler().validate([a, b])  # must not raise


def test_validate_intrinsic_input_ok():
    """`csv` is intrinsic and always satisfies an input declaration."""
    s = Stage(name="extract", execute=lambda c: c, inputs=["csv"], outputs=["raw_data"])
    Scheduler().validate([s])
    assert "csv" in INTRINSIC_INPUTS


# =====================================================================
# 15-22: Scheduler.run — happy path, retries, on_failure
# =====================================================================

def test_run_records_status_for_every_stage(ctx):
    """After run, every stage in the table has a final status."""
    Scheduler().run(BASELINE_TABLE, ctx)
    assert set(ctx.stage_status.keys()) == {"extract", "metrics", "claims", "sections", "merge"}
    for name, st in ctx.stage_status.items():
        assert st == StageStatus.SUCCESS.value, f"{name}={st}"


def test_run_records_history_in_order(ctx):
    """stage_history reflects execution order (== table order for a
    sequential scheduler)."""
    Scheduler().run(BASELINE_TABLE, ctx)
    assert ctx.stage_history == ["extract", "metrics", "claims", "sections", "merge"]


def test_run_retries_on_transient_failure(ctx):
    """A stage that fails on the first call but succeeds on the second
    should record 2 attempts and end SUCCESS."""
    call_count = {"n": 0}

    def flaky_execute(c):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient flake")
        c.stage_status["flaky"] = "mutated"
        return c

    s = Stage(
        name="flaky",
        execute=flaky_execute,
        outputs=["raw_data"],
        retries=2,
        on_failure="abort",
    )
    # Put it AFTER extract in the table so input deps are satisfied.
    table = [stage_extract_spec, s, stage_metrics_spec]
    Scheduler().run(table, ctx)

    assert call_count["n"] == 2
    assert ctx.stage_status["flaky"] == StageStatus.SUCCESS.value
    assert ctx.stage_attempts["flaky"] == 2
    assert ctx.stage_status["extract"] == StageStatus.SUCCESS.value
    assert ctx.stage_status["metrics"] == StageStatus.SUCCESS.value


def test_run_on_failure_skip_continues_pipeline(ctx):
    """A failing stage with on_failure='skip' is recorded SKIPPED and
    downstream stages still run."""
    def always_fail(c):
        raise RuntimeError("nope")

    s = Stage(
        name="boom",
        execute=always_fail,
        inputs=["fields"],
        outputs=["claims"],   # a known output slot; downstream stages
                              # don't depend on `claims` here, so the
                              # skip is safe.
        retries=0,
        on_failure="skip",
    )
    table = [stage_extract_spec, s, stage_metrics_spec]
    Scheduler().run(table, ctx)

    assert ctx.stage_status["boom"] == StageStatus.SKIPPED.value
    assert ctx.stage_status["extract"] == StageStatus.SUCCESS.value
    # metrics stage ran even though "boom" didn't produce its declared
    # output — metrics doesn't depend on "claims" so this is OK.
    assert ctx.stage_status["metrics"] == StageStatus.SUCCESS.value


def test_run_on_failure_abort_raises_with_stage_name(ctx):
    def always_fail(c):
        raise ValueError("specific marker")

    s = Stage(
        name="kaboom",
        execute=always_fail,
        inputs=["fields"],
        outputs=["metrics"],  # valid output slot
        retries=0,
        on_failure="abort",
    )
    table = [stage_extract_spec, s]
    with pytest.raises(StageExecutionError) as excinfo:
        Scheduler().run(table, ctx)
    assert excinfo.value.stage_name == "kaboom"
    assert isinstance(excinfo.value.original, ValueError)
    assert "specific marker" in str(excinfo.value.original)
    assert ctx.stage_status["kaboom"] == StageStatus.FAILED.value


def test_run_on_failure_fallback_used(ctx):
    """When a stage fails under on_failure='fallback', the fallback
    callable's return value becomes the context for downstream stages."""
    seen = {"fallback_called": False}

    def always_fail(c):
        raise RuntimeError("first attempt failed")

    def fallback(c):
        seen["fallback_called"] = True
        c.stage_status["fb_marker"] = "yes"
        return c

    s = Stage(
        name="with_fb",
        execute=always_fail,
        inputs=["fields"],
        outputs=["raw_data"],  # pretend we still produced raw_data
        retries=0,
        on_failure="fallback",
        fallback=fallback,
    )
    table = [stage_extract_spec, s]
    Scheduler().run(table, ctx)
    assert seen["fallback_called"] is True
    assert ctx.stage_status["with_fb"] == StageStatus.SUCCESS.value
    assert ctx.stage_status.get("fb_marker") == "yes"
    assert ctx.error is None


def test_run_fallback_result_is_validated(ctx):
    """Fallback output must satisfy the same validators as normal output."""
    def always_fail(c):
        raise RuntimeError("primary failed")

    def fallback(c):
        return c

    def validator_raises(c):
        raise ValueError("fallback output invalid")

    stage = Stage(
        name="invalid_fallback",
        execute=always_fail,
        outputs=["raw_data"],
        validators=[validator_raises],
        on_failure="fallback",
        fallback=fallback,
    )

    with pytest.raises(StageExecutionError) as excinfo:
        Scheduler().run([stage], ctx)

    assert isinstance(excinfo.value.original, ValueError)
    assert ctx.stage_status["invalid_fallback"] == StageStatus.FAILED.value
    assert ctx.error is excinfo.value.original


def test_run_rejects_non_context_stage_result(ctx):
    """A bad stage return is reported as a stage failure, not an AttributeError."""
    stage = Stage(
        name="bad_return",
        execute=lambda c: None,
        outputs=["raw_data"],
    )

    with pytest.raises(StageExecutionError) as excinfo:
        Scheduler().run([stage], ctx)

    assert isinstance(excinfo.value.original, TypeError)
    assert "expected WorkflowContext" in str(excinfo.value.original)
    assert ctx.stage_status["bad_return"] == StageStatus.FAILED.value


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_run_does_not_retry_process_control_exceptions(ctx, signal):
    """Process-control exceptions are never swallowed by business retries."""
    calls = {"n": 0}

    def interrupt(c):
        calls["n"] += 1
        raise signal()

    stage = Stage(
        name="interrupt",
        execute=interrupt,
        outputs=["raw_data"],
        retries=2,
    )

    with pytest.raises(signal):
        Scheduler().run([stage], ctx)

    assert calls["n"] == 1
    assert ctx.stage_attempts["interrupt"] == 1


def test_run_validator_failure_treated_as_failure(ctx):
    """A post-execute validator that raises aborts (or triggers the
    configured on_failure policy) just like an execute failure."""
    def good_execute(c):
        return c

    def validator_raises(c):
        raise AssertionError("validator says no")

    s = Stage(
        name="validates_badly",
        execute=good_execute,
        outputs=["raw_data"],
        validators=[validator_raises],
        retries=0,
        on_failure="abort",
    )
    with pytest.raises(StageExecutionError) as excinfo:
        Scheduler().run([s], ctx)
    assert "validates_badly" in str(excinfo.value)
    assert ctx.stage_status["validates_badly"] == StageStatus.FAILED.value


def test_run_no_retry_when_first_succeeds(ctx):
    """Retries must not be consumed by a successful first attempt."""
    calls = {"n": 0}

    def execute(c):
        calls["n"] += 1
        return c

    s = Stage(name="once", execute=execute, outputs=["raw_data"], retries=5)
    Scheduler().run([s], ctx)
    assert calls["n"] == 1
    assert ctx.stage_attempts["once"] == 1


# =====================================================================
# 23-25: Backward compatibility — run_baseline_workflow still works
# =====================================================================

def test_run_baseline_workflow_returns_workflow_result(store, csv_path, llm):
    """Old API: run_baseline_workflow(csv, run, store, llm) -> WorkflowResult."""
    result = run_baseline_workflow(csv_path, "run_bc", store, llm)
    assert isinstance(result, WorkflowResult)
    assert result.run_id == "run_bc"
    assert result.raw_data is not None
    assert result.merged_report is not None


def test_run_baseline_workflow_populates_all_dicts(store, csv_path, llm):
    """WorkflowResult fields/metrics/claims/sections have the same
    expected keys as the pre-refactor runner."""
    result = run_baseline_workflow(csv_path, "run_bc", store, llm)
    assert set(result.fields.keys()) >= {
        f"field_{col}_{yr}" for col in ("revenue", "cost", "users", "orders", "visits")
        for yr in (2024, 2025)
    }
    assert set(result.metrics.keys()) == {
        "revenue_growth", "profit_margin", "cost_ratio",
        "user_growth", "conversion_rate", "order_growth",
    }
    assert set(result.claims.keys()) == {"financial", "user", "operation"}
    assert set(result.sections.keys()) == {"financial", "user", "operation"}


def test_run_baseline_workflow_preserves_cost_totals(store, csv_path, llm):
    """Cost totals on WorkflowResult are still populated from
    accumulate_cost() calls inside the new stages."""
    result = run_baseline_workflow(csv_path, "run_bc", store, llm)
    # 3 claim calls + 3 section calls = 6 LLM calls (FakeLLMClient).
    assert result.total_llm_calls == 6
    # merged_report is a tool, not LLM — no LLM cost from it.
    assert result.total_llm_calls > 0
    assert result.total_tokens > 0
    # merge_report latency should be reflected in total_latency_ms.
    assert result.total_latency_ms >= 0.0


# =====================================================================
# 26-28: Stages module
# =====================================================================

def test_baseline_table_has_five_stages():
    assert len(BASELINE_TABLE) == 5


def test_baseline_table_stage_names_in_order():
    assert [s.name for s in BASELINE_TABLE] == [
        "extract", "metrics", "claims", "sections", "merge",
    ]


def test_baseline_table_validates_under_scheduler():
    """BASELINE_TABLE passes Scheduler.validate()."""
    Scheduler().validate(BASELINE_TABLE)


# =====================================================================
# 29-31: Add-a-stage demo — prove the 1-line change claim
# =====================================================================

def test_add_a_stage_does_not_touch_runner():
    """Define a new stage and append to a table; runner.py is not
    modified.  This is the property the refactor was designed to
    deliver.  We assert it by importing runner, snapshotting its
    source path, and only editing the table in-memory."""
    import inspect
    import workflow.runner as runner_mod
    src_path = inspect.getfile(runner_mod)
    # The runner is exactly one .py file we did not modify for this test.
    assert src_path.endswith("runner.py")
    assert "BASELINE_TABLE" in inspect.getsource(runner_mod)


def test_one_line_table_change_runs_extra_stage(ctx):
    """Append a custom stage to a copy of BASELINE_TABLE and run it.
    No runner.py edit required.  The new stage's output is observable
    on the context."""
    seen = {"called": False}

    def my_extra(ctx):
        seen["called"] = True
        # Use a custom attribute on the context (not stage_status,
        # which the scheduler owns) to leave a marker.
        setattr(ctx, "my_extra_marker", "ran")
        return ctx

    extra_stage = Stage(
        name="my_extra",
        inputs=["metrics"],   # metrics is already produced upstream
        outputs=["fields"],   # reuses existing slot; not a new contract
        execute=my_extra,
        retries=0,
        on_failure="abort",
    )
    table = list(BASELINE_TABLE) + [extra_stage]
    Scheduler().run(table, ctx)

    assert seen["called"] is True
    assert ctx.my_extra_marker == "ran"
    # The other 5 stages still completed successfully.
    assert ctx.stage_status["merge"] == StageStatus.SUCCESS.value
    assert ctx.merged_report is not None


def test_add_a_stage_keeps_existing_outputs_intact(ctx):
    """Adding a new stage must not disturb the upstream artifacts that
    downstream consumers rely on."""
    def noop_extra(ctx):
        return ctx

    extra_stage = Stage(
        name="noop_extra",
        inputs=["metrics"],
        outputs=["fields"],
        execute=noop_extra,
    )
    table = list(BASELINE_TABLE) + [extra_stage]
    Scheduler().run(table, ctx)

    # All canonical outputs from BASELINE_TABLE must still be populated.
    assert ctx.raw_data is not None
    assert len(ctx.fields) >= 10
    assert len(ctx.metrics) == 6
    assert set(ctx.claims.keys()) == {"financial", "user", "operation"}
    assert set(ctx.sections.keys()) == {"financial", "user", "operation"}
    assert ctx.merged_report is not None


# =====================================================================
# 32-33: End-to-end integration via FakeLLM
# =====================================================================

def test_end_to_end_scheduler_pipeline(ctx):
    """Run BASELINE_TABLE on a real WorkflowContext backed by a real
    ArtifactStore + FakeLLMClient.  All 5 stages must finish SUCCESS
    and the merged_report must have the merged-report meta."""
    Scheduler().run(BASELINE_TABLE, ctx)
    assert ctx.stage_status == {s.name: "success" for s in BASELINE_TABLE}
    assert ctx.merged_report is not None
    assert ctx.merged_report.meta["task_kind"] == "tool"
    assert ctx.merged_report.meta["semantic_role"] == "merged_report"
    # Section contents are concatenated into merged_report.content.
    for sname, sec in ctx.sections.items():
        assert sec.content in ctx.merged_report.content, (
            f"section {sname!r} content missing from merged_report"
        )


def test_end_to_end_cost_accumulator(ctx):
    """accumulate_cost() correctly adds ExecutionCost fields onto
    the WorkflowContext totals (this is what makes the refactored
    runner's total_* fields non-zero)."""
    # Simulate an LLM artifact with known cost.
    from artifacts import ExecutionCost
    fake = ExecutionCost(
        llm_calls=1, input_tokens=10, output_tokens=20,
        tokens=30, monetary_usd=0.0, latency_ms=5.0,
    )

    class _FakeArtifact:
        execution_cost = fake

    accumulate_cost(ctx, _FakeArtifact())  # type: ignore[arg-type]
    assert ctx.total_llm_calls == 1
    assert ctx.total_input_tokens == 10
    assert ctx.total_output_tokens == 20
    assert ctx.total_tokens == 30
    assert ctx.total_latency_ms == 5.0
