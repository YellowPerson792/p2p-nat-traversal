param([double]$Duration = 8, [string]$Config = ".local/remote/identity-client.json",
      [int]$PortA = 0, [int]$PortB = 0, [ValidateSet("all", "auto")][string]$Scenario = "all")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$nativePython = Join-Path $projectRoot ".venv-win/Scripts/python.exe"
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $nativePython)) {
        python -m venv .venv-win
        if ($LASTEXITCODE -ne 0) { throw "Cannot create native Windows virtual environment" }
        & $nativePython -m pip install -q -r requirements.lock
        if ($LASTEXITCODE -ne 0) { throw "Cannot install native runtime dependencies" }
    }
    & $nativePython scripts/host_experiment.py --config $Config --duration $Duration --port-a $PortA --port-b $PortB --scenario $Scenario
    if ($LASTEXITCODE -ne 0) { throw "Host experiment failed; inspect artifacts" }
} finally { Pop-Location }
