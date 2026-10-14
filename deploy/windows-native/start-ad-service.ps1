. (Join-Path $PSScriptRoot 'common.ps1')
$null = Import-ProductionEnv
$java = Join-Path $PSScriptRoot 'runtime\java\bin\java.exe'
$jar = Join-Path $PSScriptRoot 'ad-service.jar'
if (-not (Test-Path $java) -or -not (Test-Path $jar)) { throw 'Runtime Java atau JAR tidak ada.' }
& $java -Dserver.address=127.0.0.1 -jar $jar
exit $LASTEXITCODE
