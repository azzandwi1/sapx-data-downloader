param(
    [string]$FromDate,
    [string]$ToDate,
    [int]$Year,
    [int]$Month,
    [string]$Branch = "NASIONAL",
    [string]$OutDir,
    [int]$Timeout = 2700,
    [int]$MaxRetries = 3,
    [int]$RetryDelay = 5,
    [int]$MaxWorkers = 1,
    [switch]$SkipExisting
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SrcPath = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = $SrcPath

$argsList = @()
if ($FromDate -and $ToDate) {
    $argsList += "--from-date", $FromDate, "--to-date", $ToDate
} else {
    if (-not $Year -or -not $Month) {
        throw "Isi -FromDate dan -ToDate, atau isi -Year dan -Month."
    }
    $argsList += "--year", $Year, "--month", $Month
}

$argsList += "--branch", $Branch
$argsList += "--timeout", $Timeout
$argsList += "--max-retries", $MaxRetries
$argsList += "--retry-delay", $RetryDelay
$argsList += "--max-workers", $MaxWorkers
if ($OutDir) {
    $argsList += "--out-dir", $OutDir
}
if ($SkipExisting) {
    $argsList += "--skip-existing"
}

Push-Location $ProjectRoot
try {
    python -m sapx_downloader @argsList
} finally {
    Pop-Location
}
