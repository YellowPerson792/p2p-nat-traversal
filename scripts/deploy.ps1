param([Parameter(Mandatory=$true)][string]$ServerAddress, [switch]$Rollback)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$deployArgs = @("$PSScriptRoot/deploy.py", "--host", $ServerAddress)
if ($Rollback) { $deployArgs += "--rollback" }
Push-Location $projectRoot
try {
    python @deployArgs
    if ($LASTEXITCODE -ne 0) { throw "Deployment failed ($LASTEXITCODE)" }
} finally { Pop-Location }
