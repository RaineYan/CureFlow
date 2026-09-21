from __future__ import annotations

import pytest

from llm.base import LLMResponse, UsageType
from llm.task_router import (
    TaskAwareLLMClient,
    TaskRouterError,
    route_for_task,
)


class StubClient:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = 0

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        self.calls += 1
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
        return LLMResponse(
            text="ok",
            input_tokens=1,
            output_tokens=1,
            latency_ms=2.5,
            provider="stub",
            model="stub-model",
            usage_type=UsageType.SYNTHETIC,
        )


def test_claim_tasks_prefer_strong_grounded_route() -> None:
    route = route_for_task("generate_financial_claim")
    assert route.task_class == "evidence_claim"
    assert route.preferred_capability == "strong"
    assert route.grounding_required is True


def test_section_tasks_use_default_grounded_route() -> None:
    route = route_for_task("generate_user_section")
    assert route.task_class == "grounded_summary"
    assert route.preferred_capability == "default"
    assert route.grounding_required is True


def test_unknown_task_uses_general_route() -> None:
    assert route_for_task("custom_task").task_class == "general"


def test_success_is_recorded_without_provider_switch() -> None:
    client = TaskAwareLLMClient(
        StubClient(), provider="authorized", model="one-model",
    )
    response = client.generate("prompt", task_name="generate_user_claim")
    snapshot = client.snapshot()
    assert response.text == "ok"
    assert snapshot["successes"] == 1
    assert snapshot["failures"] == 0
    assert snapshot["routes"][0]["resolvedProvider"] == "authorized"
    assert snapshot["routes"][0]["silentProviderSwitch"] is False


def test_two_failures_open_circuit_and_third_call_is_blocked() -> None:
    delegate = StubClient([ValueError("one"), ValueError("two")])
    client = TaskAwareLLMClient(
        delegate, provider="p", model="m", failure_threshold=2,
    )
    with pytest.raises(ValueError, match="one"):
        client.generate("x", task_name="generate_user_claim")
    with pytest.raises(ValueError, match="two"):
        client.generate("x", task_name="generate_user_claim")
    with pytest.raises(TaskRouterError, match="熔断器"):
        client.generate("x", task_name="generate_user_claim")
    assert delegate.calls == 2
    assert client.snapshot()["circuitOpen"] is True


def test_success_resets_consecutive_failure_counter() -> None:
    delegate = StubClient([ValueError("one"), "success", ValueError("two")])
    client = TaskAwareLLMClient(
        delegate, provider="p", model="m", failure_threshold=2,
    )
    with pytest.raises(ValueError):
        client.generate("x")
    client.generate("x")
    with pytest.raises(ValueError):
        client.generate("x")
    snapshot = client.snapshot()
    assert snapshot["circuitOpen"] is False
    assert snapshot["consecutiveFailures"] == 1


def test_failure_threshold_must_be_positive() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        TaskAwareLLMClient(StubClient(), provider="p", model="m", failure_threshold=0)


def test_snapshot_is_a_copy() -> None:
    client = TaskAwareLLMClient(StubClient(), provider="p", model="m")
    client.generate("x", task_name="generate_financial_claim")
    first = client.snapshot()
    first["routes"].clear()
    assert len(client.snapshot()["routes"]) == 1


def test_snapshot_explicitly_disables_automatic_fallback() -> None:
    snapshot = TaskAwareLLMClient(
        StubClient(), provider="p", model="m",
    ).snapshot()
    assert snapshot["automaticFallback"] is False
    assert snapshot["silentProviderSwitch"] is False

