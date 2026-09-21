$ErrorActionPreference = "Stop"

$desktopRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $desktopRoot
$buildRoot = Join-Path $desktopRoot "build"
$distRoot = Join-Path $buildRoot "backend-dist"
$workRoot = Join-Path $buildRoot "backend-work"
$specRoot = Join-Path $buildRoot "backend-spec"
$hookRoot = Join-Path $PSScriptRoot "pyinstaller-hooks"
$entryPoint = Join-Path $projectRoot "cureflow_app\server.py"
$sample = Join-Path $projectRoot "data\sample_company.csv"
$exp02 = Join-Path $projectRoot "experiments\results\exp02\exp02_summary.json"
$v2 = Join-Path $projectRoot "experiments\results\v2_execution_benchmark\benchmark_data.json"

foreach ($required in @($entryPoint, $sample, $exp02)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing backend build resource: $required"
    }
}

$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", "cureflow-backend",
    "--distpath", $distRoot,
    "--workpath", $workRoot,
    "--specpath", $specRoot,
    "--paths", $projectRoot,
    "--additional-hooks-dir", $hookRoot,
    "--add-data", "$sample;data",
    "--add-data", "$exp02;experiments\results\exp02"
)
if (Test-Path -LiteralPath $v2) {
    $arguments += @("--add-data", "$v2;experiments\results\v2_execution_benchmark")
}
$arguments += $entryPoint

Push-Location $projectRoot
try {
    & python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$executable = Join-Path $distRoot "cureflow-backend\cureflow-backend.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Backend executable was not created: $executable"
}
Write-Host "BACKEND_EXE=$executable"
