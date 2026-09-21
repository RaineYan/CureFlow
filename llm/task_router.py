"""Task-aware policy router for an already-authorized LLM client.

The router never opens a provider connection and never changes provider/model
selection.  It classifies workflow tasks, records success/failure telemetry,
and stops cascading calls after repeated failures.  This keeps provider
authorization in ``DesktopProviderRuntime`` while making routing decisions
visible and testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any

from llm.base import LLMClient, LLMResponse


class TaskRouterError(RuntimeError):
    """Raised when the task-level circuit breaker blocks a call."""


@dataclass(frozen=True)
class TaskRoute:
    task_class: str
    preferred_capability: str
    grounding_required: bool
    human_review_on_risk: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskClass": self.task_class,
            "preferredCapability": self.preferred_capability,
            "groundingRequired": self.grounding_required,
            "humanReviewOnRisk": self.human_review_on_risk,
        }


CLAIM_ROUTE = TaskRoute(
    task_class="evidence_claim",
    preferred_capability="strong",
    grounding_required=True,
    human_review_on_risk=True,
)
SECTION_ROUTE = TaskRoute(
    task_class="grounded_summary",
    preferred_capability="default",
    grounding_required=True,
    human_review_on_risk=True,
)
GENERAL_ROUTE = TaskRoute(
    task_class="general",
    preferred_capability="default",
    grounding_required=False,
    human_review_on_risk=True,
)


def route_for_task(task_name: str) -> TaskRoute:
    if task_name.startswith("generate_") and task_name.endswith("_claim"):
        return CLAIM_ROUTE
    if task_name.startswith("generate_") and task_name.endswith("_section"):
        return SECTION_ROUTE
    return GENERAL_ROUTE


class TaskAwareLLMClient:
    """Apply task policy and circuit breaking to one authorized delegate.

    ``provider`` and ``model`` are labels only; this class cannot create or
    switch clients.  Therefore it cannot silently upgrade a model or enable
    live traffic.
    """

    def __init__(
        self,
        delegate: LLMClient,
        *,
        provider: str,
        model: str,
        failure_threshold: int = 2,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._delegate = delegate
        self.provider = provider
        self.model = model
        self.failure_threshold = failure_threshold
        self._lock = Lock()
        self._calls = 0
        self._successes = 0
        self._failures = 0
        self._consecutive_failures = 0
        self._circuit_open = False
        self._task_counts: dict[str, int] = {}
        self._routes: list[dict[str, Any]] = []

    @property
    def config(self) -> Any:
        """Preserve read-only provider config introspection for wrappers/tests."""
        return getattr(self._delegate, "config")

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        route = route_for_task(task_name)
        with self._lock:
            if self._circuit_open:
                raise TaskRouterError(
                    "模型任务熔断器已开启；为避免级联失败，本次后续调用已停止"
                )
            self._calls += 1
            self._task_counts[route.task_class] = (
                self._task_counts.get(route.task_class, 0) + 1
            )
        try:
            response = self._delegate.generate(prompt, task_name=task_name)
        except Exception:
            with self._lock:
                self._failures += 1
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.failure_threshold:
                    self._circuit_open = True
                self._routes.append(self._route_event(task_name, route, "failed"))
            raise
        with self._lock:
            self._successes += 1
            self._consecutive_failures = 0
            event = self._route_event(task_name, route, "success")
            event["latencyMs"] = response.latency_ms
            self._routes.append(event)
        return response

    def _route_event(
        self, task_name: str, route: TaskRoute, status: str,
    ) -> dict[str, Any]:
        return {
            "taskName": task_name,
            **route.to_dict(),
            "resolvedProvider": self.provider,
            "resolvedModel": self.model,
            "selectionBoundary": "already_authorized_provider_only",
            "silentProviderSwitch": False,
            "status": status,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "policy": "task_aware_authorized_provider_v1",
                "provider": self.provider,
                "model": self.model,
                "calls": self._calls,
                "successes": self._successes,
                "failures": self._failures,
                "consecutiveFailures": self._consecutive_failures,
                "failureThreshold": self.failure_threshold,
                "circuitOpen": self._circuit_open,
                "taskCounts": dict(self._task_counts),
                "routes": [dict(row) for row in self._routes],
                "automaticFallback": False,
                "silentProviderSwitch": False,
            }
