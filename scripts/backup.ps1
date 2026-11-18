$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'local'
$dest = Join-Path $source ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
if (-not (Test-Path $source)) { exit 0 }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Get-ChildItem $source -Filter '*.sqlite3' -File -ErrorAction SilentlyContinue | Copy-Item -Destination $dest
Write-Output "backed up to $dest"
