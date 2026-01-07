param(
  [Alias("h", "help")]
  [switch]$ShowHelp,
  [string]$TarPath = "",
  [string]$TargetDir = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$usage = @"
Usage:
  .\unpack_mlruns_windows.ps1 [-TarPath <path>] [-TargetDir <dir>]
  .\unpack_mlruns_windows.ps1 -h

Notes:
  - If -TarPath is omitted, the newest qlib_run_*.tar.gz under .\models\ is used.
  - Default TargetDir is the script directory (typically examples\).
"@

if ($ShowHelp) {
  Write-Host $usage
  exit 0
}

$scriptDir = (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not $TarPath) {
  $modelDir = Join-Path $scriptDir "models"
  if (Test-Path -LiteralPath $modelDir) {
    $candidates = @(Get-ChildItem -LiteralPath $modelDir -Filter "qlib_run_*.tar.gz" | Sort-Object LastWriteTime -Descending)
    if ($candidates.Count -gt 0) {
      $TarPath = $candidates[0].FullName
      Write-Host "Auto selected: $TarPath"
    }
  }
}

if (-not $TarPath) {
  throw "TarPath is required. Put tar.gz under models/ or pass -TarPath."
}
if (-not (Test-Path -LiteralPath $TarPath)) {
  throw "Tar file not found: $TarPath"
}

$TargetDir = (Resolve-Path -LiteralPath $TargetDir).Path
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

Write-Host "Extracting $TarPath -> $TargetDir"
tar -xzf $TarPath -C $TargetDir

$mlrunsPath = Join-Path $TargetDir "mlruns"
if (-not (Test-Path -LiteralPath $mlrunsPath)) {
  Write-Warning "mlruns directory not found after extract: $mlrunsPath"
} else {
  Write-Host "mlruns path: $mlrunsPath"
}

$mlrunsUri = "file:/" + ($mlrunsPath -replace "\\", "/")
Write-Host "Tip: run scripts from $TargetDir so Qlib finds mlruns automatically."
Write-Host "Or set: `$env:QLIB_MLFLOW_URI = '$mlrunsUri'"
