"""Fail closed when the desktop's four provider registries drift apart."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = {"openai", "qwen", "deepseek", "doubao"}


def provider_sets(root: Path = ROOT) -> dict[str, set[str]]:
    from cureflow_app.local_provider_setup import _PROVIDERS
    from cureflow_app.provider_runtime import DesktopProviderRuntime

    runtime = DesktopProviderRuntime()
    status = runtime.status()
    runtime_tree = ast.parse((root / "cureflow_app/provider_runtime.py").read_text(encoding="utf-8"))
    specs: set[str] = set()
    for node in ast.walk(runtime_tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "provider_specs" for target in node.targets) and isinstance(node.value, ast.Dict):
            for value in node.value.values:
                if isinstance(value, ast.Dict):
                    for key, item in zip(value.keys, value.values):
                        if isinstance(key, ast.Constant) and key.value == "provider" and isinstance(item, ast.Constant):
                            specs.add(item.value)
    jsx = (root / "desktop/src/App.jsx").read_text(encoding="utf-8")
    catalog = jsx.split("const providerCatalog = [", 1)[1].split("];", 1)[0]
    picker = set(re.findall(r'\bkey:"([a-z]+)"', catalog))
    return {
        "local setup": set(_PROVIDERS),
        "runtime status": set(status) & EXPECTED,
        "runtime resolver": specs,
        "desktop picker": picker,
    }


def check_consistency(sets: dict[str, set[str]]) -> list[str]:
    return [f"{place}: expected {sorted(EXPECTED)}, got {sorted(found)}" for place, found in sets.items() if found != EXPECTED]


if __name__ == "__main__":
    errors = check_consistency(provider_sets())
    if errors:
        raise SystemExit("\n".join(errors))
    print("OK: 4 providers consistent")
