"""Day 14 Pilot package.

Re-exports the public surface so callers can write
`from experiments.day14 import TrialRunner, ...`.
"""
from .frozen_evidence import (
    DAY13_FROZEN_FILES,
    load_frozen_evidence,
    verify_frozen_evidence_unchanged,
)
from .provider_config import (
    FROZEN_PROVIDER_CONFIG,
    ProviderConfig,
    validate_provider_config_match,
)
from .scenario_signature import (
    compute_scenario_signature,
    compute_scenario_signature_hash,
    verify_signature_fairness,
)
from .validators import (
    TrialSuccessReport,
    evaluate_trial_success,
    validate_new_llm_artifact,
)
from .metrics import (
    compute_provider_token_reduction,
    compute_real_llm_call_reduction,
    compute_recovery_wallclock_reduction,
)
from .recording import (
    Day14RecordingClient,
    MockHttpRealLLMClient,
    SentinelRealProviderClient,
    _redact_secrets,
    assert_not_fake_client,
)
from .strategies import (
    run_coarse_trial,
    run_full_trial,
    run_selective_trial,
)
from .pilot_runner import (
    TrialResult,
    TrialSpec,
    assert_frozen_evidence_intact,
    assert_provider_config,
    assert_signature_fairness,
    build_trial_spec,
    compute_reductions,
    is_replacement_id_occupied,
    next_replacement_id,
    root_trial_id_of,
    run_trial,
    write_trial_result,
)
