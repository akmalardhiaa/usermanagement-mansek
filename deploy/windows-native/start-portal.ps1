. (Join-Path $PSScriptRoot 'common.ps1')
$null = Import-ProductionEnv
$python = Join-Path $PSScriptRoot 'runtime\python\python.exe'
if (-not (Test-Path $python)) { throw 'Runtime Python tidak ada.' }
Set-Location (Join-Path $PSScriptRoot 'portal')
& $python -m uvicorn --factory app.main:app_factory --host 127.0.0.1 --port 3000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
exit $LASTEXITCODE
