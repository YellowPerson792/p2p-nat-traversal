param([switch]$Remote, [string]$Distribution = "Ubuntu-24.04")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$labArgs = @("scripts/experiment.py")
if ($Remote) { $labArgs += @("--remote", ".local/remote/identity-client.json") }
wsl -d $Distribution -u root --cd $projectRoot -- .venv/bin/python @labArgs
if ($LASTEXITCODE -ne 0) { throw "Experiment failed; inspect artifacts ($LASTEXITCODE)" }
