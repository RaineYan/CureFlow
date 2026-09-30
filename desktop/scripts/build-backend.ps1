$ErrorActionPreference = "Stop"

$desktopRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $desktopRoot
$buildRoot = Join-Path $desktopRoot "build"
$distRoot = Join-Path $buildRoot "backend-dist"
$workRoot = Join-Path $buildRoot "backend-work"
$specRoot = Join-Path $buildRoot "backend-spec"
$hookRoot = Join-Path $PSScriptRoot "pyinstaller-hooks"
$entryPoint = Join-Path $projectRoot "cureflow_app\server.py"
foreach ($required in @($entryPoint)) {
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
    "--additional-hooks-dir", $hookRoot
)
$arguments += $entryPoint

Push-Location $projectRoot
try {
    $pythonCommand = if ($env:CUREFLOW_PYTHON) { $env:CUREFLOW_PYTHON } else { "python" }
    & $pythonCommand @arguments
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
