$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$source = Join-Path $root 'local'
if (-not (Test-Path $source)) { exit 0 }
if (-not (Test-Path $python)) { throw 'Python venv missing for SQLite WAL backup' }
& $python -m personal_agent.cli backup
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
