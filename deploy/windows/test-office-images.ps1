# Smoke test on the current development Podman connection. No AD or real secrets.
# Uses unique resource names; existing application containers are never touched.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'podman-common.ps1')
$prefix = 'hc-smoke-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$network = "$prefix-net"; $dataVolume = "$prefix-data"; $caVolume = "$prefix-ca"
$portal = "$prefix-portal"; $ad = "$prefix-ad"; $stage = "$prefix-stage"
$createdContainers = New-Object 'System.Collections.Generic.List[string]'
$createdVolumes = New-Object 'System.Collections.Generic.List[string]'
$networkCreated = $false
$label = "org.hcportal.smoke=$prefix"
$backupArchive = Join-Path ([IO.Path]::GetTempPath()) "$prefix-backup.tar"
try {
    $null = Invoke-Podman -ArgumentList @('network', 'create', '--internal', '--label', $label, $network) -What 'Smoke network'
    $networkCreated = $true
    foreach ($volume in @($dataVolume, $caVolume)) {
        $null = Invoke-Podman -ArgumentList @('volume', 'create', '--label', $label, $volume) -What 'Smoke volume'
        $createdVolumes.Add($volume)
    }
    $null = Invoke-Podman -ArgumentList @('create', '--name', $stage, '--label', $label, '--pull=never', '--network=none',
        '--user', '0', '--volume', "${caVolume}:/run/certs", '--entrypoint', '/bin/sh', 'localhost/hc-ad-service:latest',
        '-c', 'chmod 755 /run/certs && chmod 644 /run/certs/public-test.txt') -What 'Smoke staging'
    $createdContainers.Add($stage)
    $null = Invoke-Podman -ArgumentList @('cp', (Join-Path $PSScriptRoot 'README.md'), "${stage}:/run/certs/public-test.txt") -What 'Windows to Linux copy'
    $null = Invoke-Podman -ArgumentList @('start', '--attach', $stage) -What 'Smoke permissions'
    $null = Invoke-Podman -ArgumentList @('run', '-d', '--name', $ad, '--label', $label, '--pull=never',
        '--network', $network, '--network-alias', 'ad-service', '--env', 'APP_ENV=development',
        '--env', 'AD_SERVICE_TOKEN=smoke-test-token-not-a-real-secret', '--env', 'DEMO_LOGIN=off',
        '--volume', "${caVolume}:/run/certs:ro", 'localhost/hc-ad-service:latest') -What 'Smoke Java'
    $createdContainers.Add($ad)
    $portalArgs = @('run', '-d', '--name', $portal, '--label', $label, '--pull=never', '--network', $network,
        '--publish', '127.0.0.1::3000', '--env', 'APP_ENV=development', '--env', 'DEMO_LOGIN=off',
        '--env', 'AD_SERVICE_TOKEN=smoke-test-token-not-a-real-secret', '--env', 'HC_DATA_DIR=/app/data',
        '--env', 'AD_SERVICE_URL=http://ad-service:8081', '--volume', "${dataVolume}:/app/data",
        '--volume', "${caVolume}:/run/certs:ro", '--health-cmd', 'python /app/scripts/healthcheck.py',
        '--health-interval', '5s', 'localhost/hc-portal:latest')
    $null = Invoke-Podman -ArgumentList $portalArgs -What 'Smoke portal'
    $createdContainers.Add($portal)
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        $check = Invoke-Native -FilePath podman -ArgumentList @('exec', $portal, 'python', '/app/scripts/healthcheck.py', '--ad')
        if ($check.ExitCode -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw 'Java health check failed.' }
    $null = Invoke-Podman -ArgumentList @('healthcheck', 'run', $portal) -What 'Portal health command'
    $binding = Invoke-Podman -ArgumentList @('port', $portal, '3000/tcp') -What 'Published loopback port'
    $url = 'http://' + ($binding.Output -join '').Trim() + '/healthz'
    $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 $url
    if ($response.StatusCode -ne 200) { throw 'Windows to container HTTP failed.' }
    $null = Invoke-Podman -ArgumentList @('exec', $portal, 'test', '-r', '/run/certs/public-test.txt') -What 'Read-only public file access'
    $null = Invoke-Podman -ArgumentList @('exec', $portal, 'touch', '/app/data/smoke-persistence-marker') -What 'Non-root data write'
    $null = Invoke-Podman -ArgumentList @('stop', '--time', '10', $portal) -What 'Smoke stop'
    $null = Invoke-Podman -ArgumentList @('volume', 'export', $dataVolume, '--output', $backupArchive) -What 'Remote backup export to Windows'
    if (-not (Test-Path -LiteralPath $backupArchive) -or (Get-Item -LiteralPath $backupArchive).Length -eq 0) {
        throw 'Backup archive was not created on Windows.'
    }
    $null = Invoke-Podman -ArgumentList @('rm', $portal) -What 'Smoke recreate'
    $null = Invoke-Podman -ArgumentList $portalArgs -What 'Smoke recreate with same volume'
    $null = Invoke-Podman -ArgumentList @('exec', $portal, 'test', '-f', '/app/data/smoke-persistence-marker') -What 'Data survives recreate'
    Write-Host 'PASS: real images, Windows copy, DNS alias, Java/portal HTTP, health command, non-root volume, backup export and persistence.'
} finally {
    foreach ($name in $createdContainers) { $null = Invoke-Native -FilePath podman -ArgumentList @('rm', '--force', $name) }
    foreach ($name in $createdVolumes) { $null = Invoke-Native -FilePath podman -ArgumentList @('volume', 'rm', $name) }
    if ($networkCreated) { $null = Invoke-Native -FilePath podman -ArgumentList @('network', 'rm', $network) }
    if (Test-Path -LiteralPath $backupArchive) { Remove-Item -LiteralPath $backupArchive }
}
