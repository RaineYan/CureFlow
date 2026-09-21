"""
Project-level pytest configuration.

Registers the `live_llm` marker and provides a skipif helper
for live-network tests.

Day 13.5 contract:
  - `pytest` (default)            -> 0 network calls
  - `pytest -m live_llm`          -> runs live tests; if
                                     REAL_LLM_API_KEY is unset
                                     they SKIP rather than fail.
"""
from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_llm: tests that perform real network calls to an LLM "
        "provider; only run with `pytest -m live_llm`",
    )


# Provide a single helper so live tests can `skipif_no_credential` mark
# without re-implementing the env-var check.

SKIP_REASON_NO_KEY = (
    "REAL_LLM_API_KEY not set; live_llm tests are skipped in offline mode"
)


skipif_no_credential = pytest.mark.skipif(
    not os.environ.get("REAL_LLM_API_KEY"),
    reason=SKIP_REASON_NO_KEY,
)


@pytest.fixture(autouse=True)
def _default_offline(request) -> None:
    """If a test is marked `live_llm` but the user did NOT pass
    `-m live_llm`, skip it.  This keeps `pytest` (default) at
    0 network calls."""
    marker = request.node.get_closest_marker("live_llm")
    if marker is None:
        return
    if "live_llm" not in request.config.invocation_params.args:
        # No `-m live_llm` on the CLI -> skip
        pytest.skip("live_llm test only runs with `pytest -m live_llm`")
