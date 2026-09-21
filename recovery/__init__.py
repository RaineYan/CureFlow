"""Recovery layer (Day 8 + Day 9 + Day 13.6 FINAL-FINAL + Day 14 v2).

Day 8: dependency-aware, version-aware, rule-based selective recovery.
Day 9: validate_recovery_execution_integrity — complements
       validate_target_clean_path (which is the Day-7 semantic
       contamination policy) with an execution-time input-integrity
       check.  Recovery success = semantic_clean AND execution_integrity.
Day 13.6 FINAL-FINAL: clean_path_audit — structured
       `CleanPathAuditResult` (visited_refs / non_clean_refs /
       missing_refs / reason) so a False is debuggable from the
       JSON alone.  Also REBUILDS the ArtifactGraph from the
       post-recovery store before walking the chain.
Day 14 v2: strategy market — the 3 hard-coded recovery strategies
       (SELECTIVE/COARSE/FULL) are now pluggable behind a
       `Strategy` Protocol + `StrategyRegistry`.  New strategies
       can be added by `@register_strategy("NAME")`-decorating a
       class with a `plan(cr, ctx) -> RecoveryPlan` method; the
       executor dispatches per-step through
       `REGISTRY.get(plan.strategy_name).dispatch_step(...)`.
"""
from .models import (
    CleanPathAuditResult,
    ExecutedRecoveryStep,
    RecoveryAction,
    RecoveryExecutionResult,
    RecoveryPlan,
    RecoveryStep,
)
from .planner import SelectiveRecoveryPlanner, build_recovery_plan
from .decision import Decision, DecisionEngine, build_auto_plan
from .strategy import (
    REGISTRY,
    RecoveryContext,
    Strategy,
    StrategyRegistry,
    register_strategy,
)
# Importing the package populates REGISTRY with the 3 built-in
# strategies (FULL_RERUN / COARSE_CHECKPOINT / SELECTIVE_RECOVERY).
from .strategies import (
    CoarseCheckpointStrategy,
    FullRerunStrategy,
    SelectiveRecoveryStrategy,
)
from .executor import (
    RecoveryExecutor,
    clean_path_audit,
    validate_recovery_execution_integrity,
    validate_target_clean_path,
)


__all__ = [
    "CleanPathAuditResult",
    "ExecutedRecoveryStep",
    "RecoveryAction",
    "RecoveryContext",
    "RecoveryExecutionResult",
    "RecoveryPlan",
    "RecoveryStep",
    "SelectiveRecoveryPlanner",
    "build_recovery_plan",
    "build_auto_plan",
    "Decision",
    "DecisionEngine",
    "REGISTRY",
    "Strategy",
    "StrategyRegistry",
    "register_strategy",
    "CoarseCheckpointStrategy",
    "FullRerunStrategy",
    "SelectiveRecoveryStrategy",
    "RecoveryExecutor",
    "clean_path_audit",
    "validate_target_clean_path",
    "validate_recovery_execution_integrity",
]
