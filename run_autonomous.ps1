$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Project
$Python = Join-Path $Project ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }
& $Python "$Project\autonomous_runner.py"
exit $LASTEXITCODE
