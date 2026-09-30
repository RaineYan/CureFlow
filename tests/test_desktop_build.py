"""Exercise Windows packaging orchestration without running PyInstaller/providers."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("with_research_files", [False, True])
def test_backend_build_supports_clean_checkout_and_selected_python(tmp_path, with_research_files):
    project = tmp_path / "project with spaces"
    scripts = project / "desktop" / "scripts"
    scripts.mkdir(parents=True)
    build = scripts / "build-backend.ps1"
    shutil.copyfile(ROOT / "desktop/scripts/build-backend.ps1", build)
    for name in ("cureflow_app/server.py", "data/sample_company.csv"):
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture", encoding="utf-8")
    if with_research_files:
        for name in ("exp02/exp02_summary.json", "v2_execution_benchmark/benchmark_data.json"):
            target = project / "experiments/results" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}", encoding="utf-8")
    fake_python = project / "test-python.ps1"
    fake_python.write_text('''
$ErrorActionPreference = 'Stop'
$args | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'invocation.json') -Encoding UTF8
$target = Join-Path $PSScriptRoot 'desktop/build/backend-dist/cureflow-backend'
New-Item -ItemType Directory -Path $target -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $target 'cureflow-backend.exe') | Out-Null
exit 0
''', encoding="utf-8")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(build)],
        env={**os.environ, "CUREFLOW_PYTHON": str(fake_python)},
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    args = json.loads((project / "invocation.json").read_text(encoding="utf-8-sig"))
    assert args[:2] == ["-m", "PyInstaller"]
    assert args[-1] == str(project / "cureflow_app/server.py")
    assert not any("sample_company.csv" in x for x in args)
    assert not any("exp02_summary.json" in x for x in args)
    assert not any("benchmark_data.json" in x for x in args)
    assert "BACKEND_EXE=" in result.stdout.decode(errors="replace")
