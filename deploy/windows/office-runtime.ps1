# Office runtime: Podman 6+ / Hyper-V, no Compose and no Windows bind mounts.
# Dot-source only. Resources live in the dedicated hc-office machine.
. (Join-Path $PSScriptRoot 'podman-common.ps1')
$script:OfficeMachine = 'hc-office'
$script:OfficeRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:OfficeLabel = 'org.hcportal.deployment=office-hyperv'

function Use-OfficePodman {
    foreach ($dir in @("$env:ProgramFiles\Podman", "$env:ProgramFiles\RedHat\Podman",
                       "$env:LOCALAPPDATA\Programs\Podman", "$env:LOCALAPPDATA\Microsoft\WinGet\Links")) {
        if ((Test-Path -LiteralPath $dir) -and ($env:Path -split ';') -notcontains $dir) { $env:Path += ";$dir" }
    }
    $version = Invoke-Podman -ArgumentList @('--version') -What 'Memeriksa Podman'
    if (($version.Output -join ' ') -notmatch 'version\s+(\d+)\.' -or [int]$Matches[1] -lt 6) {
        throw 'Jalur kantor ini memerlukan Podman 6 atau lebih baru. Pasang versi yang disetujui IT dengan provider Hyper-V.'
    }
    $env:CONTAINERS_MACHINE_PROVIDER = 'hyperv'
    $env:CONTAINER_CONNECTION = $script:OfficeMachine
    # Do not let an inherited remote host override the named office connection.
    Remove-Item Env:CONTAINER_HOST -ErrorAction SilentlyContinue
}

function Get-OfficeMachine {
    $result = Invoke-Podman -ArgumentList @('machine', 'list', '--format', 'json') -What 'Daftar mesin Hyper-V'
    $items = @(($result.Output -join "`n") | ConvertFrom-Json)
    $machine = $items | Where-Object { $_.Name -eq $script:OfficeMachine } | Select-Object -First 1
    if ($machine -and $machine.VMType -ne 'hyperv') { throw 'hc-office bukan mesin Hyper-V. Tidak ada mesin yang diubah.' }
    return $machine
}

function Wait-OfficePodman {
    Use-OfficePodman
    if (-not (Get-OfficeMachine)) { throw 'Mesin hc-office belum ada. Jalankan deploy/windows/kantor-siapkan.ps1 dengan akun operator yang sama.' }
    # Starting an already running machine can return nonzero. Engine readiness decides.
    $null = Invoke-Native -FilePath podman -ArgumentList @('machine', 'start', $script:OfficeMachine)
    $deadline = (Get-Date).AddSeconds(120)
    do {
        if ((Invoke-Native -FilePath podman -ArgumentList @('info')).ExitCode -eq 0) { return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw 'hc-office belum siap. Periksa Hyper-V, nested virtualization, akun operator, dan podman machine list.'
}

function Assert-OfficeConfig {
    $path = Join-Path $script:OfficeRoot '.env.production.local'
    if (-not (Test-Path -LiteralPath $path)) { throw 'Jalankan deploy/windows/kantor-siapkan.ps1 untuk membuat .env.production.local.' }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $path) {
        if ($line -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') {
            $key = $Matches[1]; $value = $Matches[2]
            if ($values.ContainsKey($key)) { throw "Pengaturan ganda: $key" }
            if ($value -match '<[^<>@]+>') { throw "Isi atau kosongkan pengaturan opsional: $key" }
            $values[$key] = $value
        }
    }
    foreach ($key in @('AD_SERVICE_TOKEN', 'OUTBOX_ENCRYPTION_KEY', 'APP_BASE_URL', 'AD_LDAP_URL',
                        'AD_BASE_DN', 'LDAP_DOMAIN', 'AD_BIND_DN', 'AD_BIND_PASSWORD', 'AD_MANAGED_OUS',
                        'AD_OU_STANDARD', 'AD_ACCESS_GROUP_BASE')) {
        if ([string]::IsNullOrWhiteSpace($values[$key])) { throw "Pengaturan wajib belum diisi: $key" }
    }
    if ($values.APP_BASE_URL -notmatch '^https://[^/\s]+') { throw 'APP_BASE_URL harus alamat HTTPS portal yang disiapkan di IIS.' }
    if ($values.AD_LDAP_URL -notmatch '^ldaps://[^/:\s]+(:636)?/?$') { throw 'AD_LDAP_URL harus ldaps://nama-dc:636.' }
    if ($values.LDAP_CA_CERT_PATH -ne '/run/certs/corp-root-ca.pem') { throw 'Gunakan LDAP_CA_CERT_PATH=/run/certs/corp-root-ca.pem.' }
    if ($values.SMTP_CA_CERT_PATH -and $values.SMTP_CA_CERT_PATH -ne '/run/certs/corp-root-ca.pem') {
        throw 'Bundle CA SMTP juga harus memakai /run/certs/corp-root-ca.pem pada jalur kantor ini.'
    }
    if ($values.EMAIL_REDIRECT_TO) { throw 'EMAIL_REDIRECT_TO harus kosong di kantor.' }
    if ($values.AD_SERVICE_TOKEN.Length -lt 24) { throw 'AD_SERVICE_TOKEN minimal 24 karakter.' }
    try { $bytes = [Convert]::FromBase64String($values.OUTBOX_ENCRYPTION_KEY) } catch { throw 'OUTBOX_ENCRYPTION_KEY harus base64.' }
    if ($bytes.Length -ne 32) { throw 'OUTBOX_ENCRYPTION_KEY harus berisi 32 byte.' }
    $cert = Join-Path $script:OfficeRoot 'certs\corp-root-ca.pem'
    if (-not (Test-Path -LiteralPath $cert) -or (Get-Content -LiteralPath $cert -Raw) -notmatch '-----BEGIN CERTIFICATE-----') {
        throw 'Simpan bundle CA publik dalam format PEM di certs\corp-root-ca.pem.'
    }
}

function Test-OfficeContainer {
    param([string]$Name)
    $exists = Invoke-Native -FilePath podman -ArgumentList @('container', 'exists', $Name)
    if ($exists.ExitCode -eq 1) { return $false }
    if ($exists.ExitCode -ne 0) { throw "Gagal memeriksa container $Name." }
    $info = Invoke-Podman -ArgumentList @('container', 'inspect', $Name) -What "Memeriksa kepemilikan $Name"
    $container = @(($info.Output -join "`n") | ConvertFrom-Json)[0]
    if ($container.Config.Labels.'org.hcportal.deployment' -ne 'office-hyperv') { throw "Container $Name bukan milik deployment kantor ini. Tidak diubah." }
    return $true
}

function Ensure-OfficeResources {
    foreach ($spec in @(@('network', 'hc-office-net'), @('volume', 'hc-portal-data'), @('volume', 'hc-portal-certs'))) {
        $kind = $spec[0]; $name = $spec[1]
        $exists = Invoke-Native -FilePath podman -ArgumentList @($kind, 'exists', $name)
        if ($exists.ExitCode -eq 1) {
            $null = Invoke-Podman -ArgumentList @($kind, 'create', '--label', $script:OfficeLabel, $name) -What "Membuat $name"
        } elseif ($exists.ExitCode -ne 0) { throw "Gagal memeriksa $name." }
        else {
            $info = Invoke-Podman -ArgumentList @($kind, 'inspect', $name) -What "Memeriksa $name"
            $resource = @(($info.Output -join "`n") | ConvertFrom-Json)[0]
            if ($resource.Labels.'org.hcportal.deployment' -ne 'office-hyperv') { throw "$name sudah ada tanpa label deployment kantor. Tidak dipakai otomatis." }
        }
    }
}

function Copy-OfficeCertificate {
    # podman cp works across the Windows remote client boundary, without drive sharing.
    $stage = 'hc-cert-' + [guid]::NewGuid().ToString('N')
    $created = $false
    try {
        $null = Invoke-Podman -ArgumentList @('create', '--name', $stage, '--pull=never', '--network=none', '--user', '0',
            '--volume', 'hc-portal-certs:/run/certs', '--entrypoint', '/bin/sh', 'localhost/hc-ad-service:latest',
            '-c', 'chmod 755 /run/certs && chmod 644 /run/certs/corp-root-ca.pem') -What 'Menyiapkan salinan CA'
        $created = $true
        $null = Invoke-Podman -ArgumentList @('cp', (Join-Path $script:OfficeRoot 'certs\corp-root-ca.pem'), "${stage}:/run/certs/corp-root-ca.pem") -What 'Menyalin CA'
        $null = Invoke-Podman -ArgumentList @('start', '--attach', $stage) -What 'Mengatur izin CA'
    } finally {
        if ($created) { $null = Invoke-Native -FilePath podman -ArgumentList @('rm', $stage) }
    }
}

function Start-OfficeStack {
    param([switch]$Recreate)
    Assert-OfficeConfig
    foreach ($imageName in @('localhost/hc-portal:latest', 'localhost/hc-ad-service:latest')) {
        $null = Invoke-Podman -ArgumentList @('image', 'inspect', '--format', '{{.Id}}', $imageName) -What "Image belum dimuat: $imageName"
    }
    # Validate BOTH names before any stop/remove, and never remove volumes.
    $portalExists = Test-OfficeContainer 'hc-portal'
    $adExists = Test-OfficeContainer 'hc-ad-service'
    Ensure-OfficeResources
    Copy-OfficeCertificate
    if ($Recreate) {
        foreach ($name in @('hc-portal', 'hc-ad-service')) {
            if (Test-OfficeContainer $name) {
                $null = Invoke-Podman -ArgumentList @('stop', '--time', '30', $name) -What "Menghentikan $name"
                $null = Invoke-Podman -ArgumentList @('rm', $name) -What "Mengganti container $name (volume tetap)"
            }
        }
        $portalExists = $false; $adExists = $false
    }
    $shared = @('--pull=never', '--label', $script:OfficeLabel, '--network', 'hc-office-net',
        '--restart=always', '--env-file', (Join-Path $script:OfficeRoot '.env.production.local'),
        '--env', 'APP_ENV=production', '--env', 'DEMO_LOGIN=off', '--volume', 'hc-portal-certs:/run/certs:ro')
    if (-not $adExists) {
        $null = Invoke-Podman -ArgumentList (@('create', '--name', 'hc-ad-service', '--network-alias', 'ad-service') + $shared +
            @('localhost/hc-ad-service:latest')) -What 'Membuat layanan AD'
    }
    if (-not $portalExists) {
        $health = 'python /app/scripts/healthcheck.py'
        $null = Invoke-Podman -ArgumentList (@('create', '--name', 'hc-portal') + $shared +
            @('--publish', '127.0.0.1:3000:3000', '--volume', 'hc-portal-data:/app/data',
              '--env', 'HC_DATA_DIR=/app/data', '--env', 'AD_SERVICE_URL=http://ad-service:8081',
              '--health-cmd', $health, '--health-interval', '30s', '--health-timeout', '5s',
              '--health-retries', '3', '--health-start-period', '20s', 'localhost/hc-portal:latest')) -What 'Membuat portal'
    }
    $null = Invoke-Podman -ArgumentList @('start', 'hc-ad-service', 'hc-portal') -What 'Menyalakan aplikasi'
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:3000/healthz'
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        if ($i % 10 -eq 0) { Write-Host 'Menunggu portal siap...' }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw 'Portal belum siap. Jalankan deploy/windows/office-status.ps1.' }
    # Auth health proves that Java is answering; directory=ldaps alone is NOT a live bind test.
    $adReady = $false
    for ($i = 0; $i -lt 30; $i++) {
        $probe = Invoke-Native -FilePath podman -ArgumentList @('exec', 'hc-portal', 'python', '/app/scripts/healthcheck.py', '--ad')
        if ($probe.ExitCode -eq 0) { $adReady = $true; break }
        if ($i % 10 -eq 0) { Write-Host 'Menunggu layanan Java AD siap...' }
        Start-Sleep -Seconds 2
    }
    if (-not $adReady) { throw 'HTTP layanan AD belum menjawab. Jalankan deploy/windows/office-status.ps1.' }
    Write-Host 'Portal dan layanan AD menjawab. Uji bind AD, OU, dan email di halaman Status sistem melalui HTTPS IIS.'
}
