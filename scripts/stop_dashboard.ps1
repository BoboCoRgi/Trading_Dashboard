$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Investment Intelligence"
    Write-Host "[FAIL] Python virtual environment"
    Write-Host "Missing .venv. Create it in the project root, then try again."
    exit 1
}
& $Python -m scripts.dashboard_launch --stop
exit $LASTEXITCODE
