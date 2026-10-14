$ErrorActionPreference = 'Stop'
$script:Root = Join-Path $env:ProgramData 'HCUserManagement'
$script:Config = Join-Path $script:Root 'config\production.env'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Jalankan PowerShell sebagai Administrator.'
    }
}

function Assert-ServiceOwned([object]$service, [string]$InstallDir, [string]$ExecutableName) {
    if (-not $service) { return }
    $expected = Join-Path ([IO.Path]::GetFullPath($InstallDir).TrimEnd('\')) $ExecutableName
    $path = [string]$service.PathName
    if (-not $path.StartsWith(('"' + $expected + '"'), [StringComparison]::OrdinalIgnoreCase) -and
        -not $path.Equals($expected, [StringComparison]::OrdinalIgnoreCase) -and
        -not $path.StartsWith(($expected + ' '), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Service $($service.Name) sudah dipakai instalasi lain."
    }
}

function Read-ProductionEnv {
    if (-not (Test-Path -LiteralPath $script:Config -PathType Leaf)) { throw "Konfigurasi belum ada: $script:Config" }
    $values = @{}
    $encoding = New-Object Text.UTF8Encoding($false, $true)
    foreach ($line in [IO.File]::ReadAllLines($script:Config, $encoding)) {
        $line = $line.TrimStart([char]0xFEFF).Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        $at = $line.IndexOf('=')
        if ($at -lt 1) { throw 'Baris konfigurasi tidak valid (harus NAMA=nilai).' }
        $key = $line.Substring(0, $at).Trim()
        if ($key -cnotmatch '^[A-Z][A-Z0-9_]*$' -or $values.ContainsKey($key)) {
            throw "Nama konfigurasi tidak valid atau duplikat: $key"
        }
        $value = $line.Substring($at + 1)
        if ($value.Contains([char]0)) { throw "Nilai $key mengandung karakter NUL." }
        $values[$key] = $value
    }
    return $values
}

function Assert-ProductionEnv([hashtable]$values) {
    foreach ($name in $values.Keys) {
        if ($values[$name] -match '<[^>]+>') { throw "Ganti placeholder $name dalam production.env." }
    }
    foreach ($name in @(
        'APP_BASE_URL','AD_SERVICE_TOKEN','OUTBOX_ENCRYPTION_KEY','AD_LDAP_URL','LDAP_DOMAIN',
        'AD_BASE_DN','LDAP_CA_CERT_PATH','AD_BIND_DN','AD_BIND_PASSWORD','AD_GROUP_HC',
        'AD_GROUP_ADMIN','AD_GROUP_OPS','AD_GROUP_AUDITOR','AD_MANAGED_OUS',
        'AD_QUARANTINE_OU','AD_ACCESS_GROUP_BASE','AD_OU_INTERNAL_PUSAT_PARENT',
        'AD_OU_INTERNAL_CABANG_PARENT','AD_OU_EXTERNAL_PUSAT',
        'AD_OU_EXTERNAL_CABANG_PARENT','AD_OU_INTERN','AD_COMPANY_NAME','AD_SYNC_OUS')) {
        if (-not $values[$name]) { throw "Isi $name dalam production.env." }
    }
    if (-not $values.CISO_APPROVER_GROUP -and -not $values.CISO_APPROVER_EMAILS) {
        throw 'Isi CISO_APPROVER_GROUP atau CISO_APPROVER_EMAILS.'
    }
    if ($values.AD_OU_ROUTING_MODE -cne 'STRUCTURED') {
        throw 'AD_OU_ROUTING_MODE=STRUCTURED wajib untuk struktur OU produksi.'
    }
    $managed = @($values.AD_MANAGED_OUS -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $sync = @($values.AD_SYNC_OUS -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if (@($managed | Where-Object { $_ -ieq $values.AD_BASE_DN }).Count) {
        throw 'AD_MANAGED_OUS tidak boleh berisi AD_BASE_DN (seluruh domain).'
    }
    $allowedOuLabels = @('Capital Market','CEO Functional','Generic','Intren','Invesment Banking',
                         'Operations','Retail','Settelment and Custody','Tecnology','Bandung','Batam',
                         'Jakarta','Makasar','Medan','Semarang','Singapore Subsidiary','Surabaya')
    $mappedLabels = @{}
    foreach ($part in @($values.AD_OU_NAME_MAP -split ';' | Where-Object { $_.Trim() })) {
        $pair = $part -split '=>', 2
        if ($pair.Count -ne 2 -or -not $pair[0].Trim() -or -not $pair[1].Trim() -or
            $allowedOuLabels -notcontains $pair[0].Trim() -or $mappedLabels.ContainsKey($pair[0].Trim())) {
            throw "AD_OU_NAME_MAP tidak valid: $($part.Trim())."
        }
        $mappedLabels[$pair[0].Trim()] = $pair[1].Trim()
    }
    $actualOuNames = @{}
    foreach ($label in $allowedOuLabels) {
        $actual = if ($mappedLabels.ContainsKey($label)) { $mappedLabels[$label] } else { $label }
        if ($actualOuNames.ContainsKey($actual)) {
            throw "AD_OU_NAME_MAP mengarahkan dua pilihan ke OU yang sama: $actual."
        }
        $actualOuNames[$actual] = $true
    }
    foreach ($name in @('AD_OU_INTERNAL_PUSAT_PARENT','AD_OU_INTERNAL_CABANG_PARENT',
                        'AD_OU_EXTERNAL_PUSAT','AD_OU_EXTERNAL_CABANG_PARENT','AD_OU_INTERN','AD_QUARANTINE_OU')) {
        $dn = $values[$name].Trim()
        if (-not ($dn -ieq $values.AD_BASE_DN -or $dn.EndsWith(',' + $values.AD_BASE_DN, [StringComparison]::OrdinalIgnoreCase))) {
            throw "$name harus berada dalam AD_BASE_DN."
        }
        if (-not @($managed | Where-Object { $dn -ieq $_ -or $dn.EndsWith(',' + $_, [StringComparison]::OrdinalIgnoreCase) }).Count) {
            throw "$name harus berada dalam AD_MANAGED_OUS."
        }
        if (-not @($sync | Where-Object { $dn -ieq $_ -or $dn.EndsWith(',' + $_, [StringComparison]::OrdinalIgnoreCase) }).Count) {
            throw "$name harus berada dalam AD_SYNC_OUS."
        }
    }
    if ($values.APP_ENV -cne 'production' -or $values.DEMO_LOGIN -cne 'off') {
        throw 'APP_ENV=production dan DEMO_LOGIN=off wajib.'
    }
    if ($values.LOGIN_2FA -cne 'on') { throw 'LOGIN_2FA=on wajib di production.' }
    if ($values.AD_LDAP_URL -cnotmatch '^ldaps://[A-Za-z0-9.-]+:636$') {
        throw 'AD_LDAP_URL harus ldaps://nama-host:636.'
    }
    if ($values.APP_BASE_URL -cnotmatch '^https://[A-Za-z0-9.-]+(:443)?$') {
        throw 'APP_BASE_URL harus HTTPS dengan nama DNS portal.'
    }
    if ($values.AD_SERVICE_URL -cne 'http://127.0.0.1:8081') {
        throw 'AD_SERVICE_URL harus http://127.0.0.1:8081.'
    }
    if ($values.AD_SERVICE_TOKEN.Length -lt 32) { throw 'AD_SERVICE_TOKEN minimal 32 karakter acak.' }
    try { $key = [Convert]::FromBase64String($values.OUTBOX_ENCRYPTION_KEY) }
    catch { throw 'OUTBOX_ENCRYPTION_KEY harus base64 dari 32 byte acak.' }
    if ($key.Length -ne 32) { throw 'OUTBOX_ENCRYPTION_KEY harus base64 dari 32 byte acak.' }
    if ($values.AD_LDAP_WRITE_ENABLED -cnotin @('true','false')) {
        throw 'AD_LDAP_WRITE_ENABLED harus true atau false.'
    }
    if (-not [IO.Path]::IsPathRooted($values.LDAP_CA_CERT_PATH) -or
        -not (Test-Path -LiteralPath $values.LDAP_CA_CERT_PATH -PathType Leaf)) {
        throw 'LDAP_CA_CERT_PATH harus path absolut ke sertifikat CA yang sudah ada.'
    }
    if ($values.EMAIL_REDIRECT_TO) { throw 'EMAIL_REDIRECT_TO tidak diizinkan di production.' }
    if ($values.HC_DATA_DIR -and
        [IO.Path]::GetFullPath($values.HC_DATA_DIR).TrimEnd('\') -ine
        [IO.Path]::GetFullPath((Join-Path $script:Root 'data')).TrimEnd('\')) {
        throw 'HC_DATA_DIR harus mengarah ke folder data ProgramData yang dipakai service.'
    }
    switch ($values.EMAIL_DRIVER) {
        'smtp' {
            foreach ($name in @('SMTP_HOST','SMTP_SENDER')) {
                if (-not $values[$name]) { throw "Isi $name untuk EMAIL_DRIVER=smtp." }
            }
            if ($values.SMTP_TLS -cnotin @('required','off')) { throw 'SMTP_TLS harus required atau off.' }
            if ([bool]$values.SMTP_USER -xor [bool]$values.SMTP_PASSWORD) {
                throw 'SMTP_USER dan SMTP_PASSWORD harus diisi bersama atau sama-sama kosong.'
            }
            if ($values.SMTP_TLS -ceq 'off' -and $values.SMTP_USER) {
                throw 'SMTP_TLS=off tidak boleh dipakai bersama autentikasi SMTP.'
            }
            $port = 0
            if (-not [int]::TryParse($values.SMTP_PORT, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
                throw 'SMTP_PORT harus angka port 1..65535.'
            }
        }
        'graph' {
            foreach ($name in @('GRAPH_TENANT_ID','GRAPH_CLIENT_ID','GRAPH_CLIENT_SECRET','GRAPH_SENDER_MAILBOX')) {
                if (-not $values[$name]) { throw "Isi $name untuk EMAIL_DRIVER=graph." }
            }
        }
        default { throw 'EMAIL_DRIVER harus smtp atau graph.' }
    }
    foreach ($name in @('SESSION_IDLE_TTL_MINUTES','SESSION_ABSOLUTE_TTL_MINUTES','APPROVAL_TOKEN_TTL_HOURS')) {
        if ($values[$name]) {
            $number = 0
            if (-not [int]::TryParse($values[$name], [ref]$number) -or $number -lt 1) {
                throw "$name harus bilangan bulat positif."
            }
        }
    }
}

function Import-ProductionEnv {
    $values = Read-ProductionEnv
    Assert-ProductionEnv $values
    foreach ($name in $values.Keys) { [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process') }
    [Environment]::SetEnvironmentVariable('HC_DATA_DIR', (Join-Path $script:Root 'data'), 'Process')
    return $values
}
