$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$log = Join-Path $root 'local\sync.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
if (-not (Test-Path $python)) { Add-Content $log "$(Get-Date -Format o) python missing"; exit 1 }
& $python -m personal_agent.cli check-config *> $null
if ($LASTEXITCODE -ne 0) { Add-Content $log "$(Get-Date -Format o) config unreadable"; exit 1 }
$config = Get-Content (Join-Path $root 'config\personal.json') -Raw | ConvertFrom-Json
if (-not $config.finance.flex_queries -or @($config.finance.flex_queries).Count -eq 0) {
  Add-Content $log "$(Get-Date -Format o) flex queries not configured; last snapshot kept"
  exit 0
}
foreach ($q in @($config.finance.flex_queries)) {
  $token = [Environment]::GetEnvironmentVariable($q.token_env, 'User')
  if (-not $token) { $token = [Environment]::GetEnvironmentVariable($q.token_env, 'Process') }
  $qid = [Environment]::GetEnvironmentVariable($q.query_env, 'User')
  if (-not $qid) { $qid = [Environment]::GetEnvironmentVariable($q.query_env, 'Process') }
  if (-not $token -or -not $qid) {
    Add-Content $log "$(Get-Date -Format o) flex credentials absent; last snapshot kept"
    exit 0
  }
  [Environment]::SetEnvironmentVariable($q.token_env, $token, 'Process')
  [Environment]::SetEnvironmentVariable($q.query_env, $qid, 'Process')
}
foreach ($envField in @('alpaca_key_env','alpaca_secret_env','finnhub_key_env','fred_key_env')) {
  $envName = $config.market.$envField
  if ($envName) {
    $val = [Environment]::GetEnvironmentVariable($envName, 'User')
    if ($val) { [Environment]::SetEnvironmentVariable($envName, $val, 'Process') }
  }
}
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -m personal_agent.cli sync-flex > $null 2>&1
$flexCode = $LASTEXITCODE
if ($flexCode -ne 0) { $ErrorActionPreference = $prevEap; Add-Content $log "$(Get-Date -Format o) sync failed; last snapshot kept"; exit 1 }
& $python -m personal_agent.cli market-sync > $null 2>&1
& $python -m personal_agent.cli run-monitoring > $null 2>&1
& $python -m personal_agent.cli report > $null 2>&1
$repCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($repCode -ne 0) { Add-Content $log "$(Get-Date -Format o) sync ok; report failed"; exit 1 }
Add-Content $log "$(Get-Date -Format o) sync ok; market and report updated"
