param(
    [string]$InstallerPath,
    [string]$ApplicationPath,
    [string]$BackendPath,
    [string]$ExpectedThumbprint
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$desktopRoot = Split-Path -Parent $PSScriptRoot
$package = Get-Content -LiteralPath (Join-Path $desktopRoot "package.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$releaseRoot = Join-Path $desktopRoot "release"
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $releaseRoot "CureFlow-Setup-$($package.version)-x64.exe"
}
if (-not $ApplicationPath) {
    $ApplicationPath = Join-Path $releaseRoot "win-unpacked\CureFlow.exe"
}
if (-not $BackendPath) {
    $BackendPath = Join-Path $releaseRoot "win-unpacked\resources\backend\cureflow-backend.exe"
}

$expected = if ($ExpectedThumbprint) { $ExpectedThumbprint.Replace(" ", "").ToUpperInvariant() } else { "" }
$signatures = @()
foreach ($item in @(
    @{ Name = "installer"; Path = $InstallerPath },
    @{ Name = "desktop"; Path = $ApplicationPath },
    @{ Name = "backend"; Path = $BackendPath }
)) {
    if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf)) {
        throw "Release file missing ($($item.Name)): $($item.Path)"
    }
    $file = (Resolve-Path -LiteralPath $item.Path).Path
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or -not $signature.SignerCertificate) {
        throw "Release file has no valid trusted signature ($($item.Name)): $file [$($signature.Status)]"
    }
    $thumbprint = $signature.SignerCertificate.Thumbprint.ToUpperInvariant()
    if ($expected -and $thumbprint -ne $expected) {
        throw "Signer thumbprint mismatch ($($item.Name)): $file"
    }
    $signatures += $thumbprint
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    Write-Output "$($item.Name): SIGNED; SHA256=$hash; SHA1_CERT=$thumbprint"
}
if (@($signatures | Select-Object -Unique).Count -ne 1) {
    throw "Installer, desktop program and backend are not signed by the same certificate"
}
Write-Output "PASS: 3 trusted signatures match; clean-PC installation and data acceptance remain separate gates"
