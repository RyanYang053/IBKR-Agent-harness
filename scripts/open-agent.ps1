$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $HOME '.dsh' }
$log = Join-Path $dshHome 'dsh-web.log'
$startCmd = Join-Path $dshHome 'start-web.cmd'
$python = Join-Path $root '.venv\Scripts\python.exe'
$dashboard = Join-Path $root 'dashboard.py'

$listening = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
if (-not $listening -and (Test-Path $startCmd)) {
  Start-Process -WindowStyle Hidden -FilePath 'cmd.exe' -ArgumentList ('/c "' + $startCmd + '"')
  Start-Sleep -Seconds 3
}
$dash = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if (-not $dash -and (Test-Path $python)) {
  Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList ('-m streamlit run "' + $dashboard + '" --server.address 127.0.0.1 --server.port 8501 --server.headless true')
  Start-Sleep -Seconds 2
}
Start-Process 'http://127.0.0.1:8501'
if (Test-Path $log) {
  $line = Get-Content $log | Where-Object { $_ -match 'http://127\.0\.0\.1:3080/\?token=' } | Select-Object -Last 1
  if ($line -match '(http://127\.0\.0\.1:3080/\?token=\S+)') {
    Start-Process $Matches[1]
  }
}
