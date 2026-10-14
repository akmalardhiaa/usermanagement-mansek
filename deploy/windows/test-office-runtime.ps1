# Standalone mocked regression tests: no real Podman, Hyper-V or AD operations.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'office-runtime.ps1')
$script:TestRoot = Join-Path ([IO.Path]::GetTempPath()) ('hc-deploy-test-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path (Join-Path $script:TestRoot 'certs') -Force
$script:OfficeRoot = $script:TestRoot
$fixture = @'
AD_SERVICE_TOKEN=01234567890123456789012345678901
OUTBOX_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
APP_BASE_URL=https://hc.example.test
AD_LDAP_URL=ldaps://dc.example.test:636
AD_BASE_DN=DC=example,DC=test
LDAP_DOMAIN=example.test
AD_BIND_DN=CN=svc,DC=example,DC=test
AD_BIND_PASSWORD=fixture-only
AD_MANAGED_OUS=OU=Test,DC=example,DC=test
AD_OU_STANDARD=OU=Test,DC=example,DC=test
AD_ACCESS_GROUP_BASE=CN=Base,DC=example,DC=test
LDAP_CA_CERT_PATH=/run/certs/corp-root-ca.pem
'@
$configPath = Join-Path $script:TestRoot '.env.production.local'
[IO.File]::WriteAllText($configPath, $fixture)
[IO.File]::WriteAllText((Join-Path $script:TestRoot 'certs\corp-root-ca.pem'), '-----BEGIN CERTIFICATE-----')

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
}
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $message = ''
    try { & $Action } catch { $message = $_.Exception.Message }
    Assert-True ($message -match $Pattern) "Expected failure $Pattern, got $message"
}
$script:Calls = New-Object 'System.Collections.Generic.List[object]'
$script:Existing = $false
$script:Foreign = $false
$script:EngineFailure = $false
$script:MissingImage = $false
$script:Provider = 'hyperv'
function Invoke-Native {
    param([string]$FilePath, [string[]]$ArgumentList, [scriptblock]$OnLine)
    $script:Calls.Add($ArgumentList)
    $code = 0; $output = @()
    if ($ArgumentList[0] -eq '--version') { $output = @('podman version 6.0.0') }
    elseif ($ArgumentList[0] -eq 'machine' -and $ArgumentList[1] -eq 'list') {
        $output = @('[{"Name":"hc-office","VMType":"' + $script:Provider + '"}]')
    }
    elseif ($ArgumentList[1] -eq 'exists') {
        if ($script:EngineFailure) { $code = 125 }
        elseif (-not $script:Existing) { $code = 1 }
    }
    elseif ($ArgumentList[0] -eq 'container' -and $ArgumentList[1] -eq 'inspect') {
        $label = if ($script:Foreign) { 'another-app' } else { 'office-hyperv' }
        $output = @('[{"Config":{"Labels":{"org.hcportal.deployment":"' + $label + '"}}}]')
    }
    elseif ($ArgumentList[0] -eq 'image' -and $script:MissingImage) { $code = 125 }
    elseif ($ArgumentList[1] -eq 'inspect' -and $ArgumentList[0] -in @('network', 'volume')) {
        $output = @('[{"Labels":{"org.hcportal.deployment":"office-hyperv"}}]')
    }
    [pscustomobject]@{ExitCode=$code; Output=$output}
}
# Match the switch parameter used by production code.
function Invoke-WebRequest { param([switch]$UseBasicParsing, $TimeoutSec, [Parameter(Position=0)]$Uri) [pscustomobject]@{StatusCode=200} }
try {
    Assert-OfficeConfig
    [IO.File]::WriteAllText($configPath, $fixture.Replace('ldaps://dc.example.test:636', 'ldap://dc.example.test:389'))
    Assert-Throws { Assert-OfficeConfig } 'AD_LDAP_URL'
    [IO.File]::WriteAllText($configPath, $fixture.Replace('https://hc.example.test', 'http://hc.example.test'))
    Assert-Throws { Assert-OfficeConfig } 'HTTPS'
    [IO.File]::WriteAllText($configPath, $fixture + "`nAD_BIND_PASSWORD=duplicate")
    Assert-Throws { Assert-OfficeConfig } 'ganda'
    [IO.File]::WriteAllText($configPath, $fixture)

    [IO.File]::WriteAllText($configPath, $fixture + "`nCISO_APPROVER_EMAILS=Test Person <test@example.test>")
    Assert-OfficeConfig
    [IO.File]::WriteAllText($configPath, $fixture)

    Use-OfficePodman
    Assert-True ($env:CONTAINERS_MACHINE_PROVIDER -eq 'hyperv') 'provider must be hyperv'
    Assert-True ($env:CONTAINER_CONNECTION -eq 'hc-office') 'must select dedicated connection'
    $script:Provider = 'wsl'
    Assert-Throws { Get-OfficeMachine } 'bukan mesin Hyper-V'
    $script:Provider = 'hyperv'
    Wait-OfficePodman

    $script:Calls.Clear()
    Start-OfficeStack
    $creates = @($script:Calls | Where-Object { $_[0] -eq 'create' })
    $portal = $creates | Where-Object { $_ -contains 'hc-portal' }
    $ad = $creates | Where-Object { $_ -contains 'hc-ad-service' }
    Assert-True ($portal -contains '127.0.0.1:3000:3000') 'portal must only publish localhost'
    Assert-True (-not ($ad -contains '--publish')) 'AD must have no published port'
    Assert-True ($portal -contains 'hc-portal-data:/app/data') 'data must use persistent named volume'
    Assert-True ($portal -contains 'hc-portal-certs:/run/certs:ro') 'CA must be mounted read-only in portal'
    Assert-True ($ad -contains 'hc-portal-certs:/run/certs:ro') 'CA must be mounted read-only in Java'
    Assert-True ($portal -contains 'APP_ENV=production') 'production must be explicit'
    Assert-True ($portal -contains 'DEMO_LOGIN=off') 'demo must be off'
    Assert-True (@($script:Calls | Where-Object { $_[0] -eq 'compose' }).Count -eq 0) 'no Compose calls'

    $script:Existing = $true; $script:Foreign = $true
    $script:Calls.Clear()
    Assert-Throws { Start-OfficeStack -Recreate } 'bukan milik'
    Assert-True (@($script:Calls | Where-Object { $_[0] -in @('stop', 'rm', 'create') }).Count -eq 0) 'foreign resource must block mutations'

    $script:Foreign = $false; $script:MissingImage = $true
    $script:Calls.Clear()
    Assert-Throws { Start-OfficeStack -Recreate } 'Image belum dimuat'
    Assert-True (@($script:Calls | Where-Object { $_[0] -in @('stop', 'rm') }).Count -eq 0) 'missing image must not stop live containers'

    $script:MissingImage = $false; $script:EngineFailure = $true
    Assert-Throws { Test-OfficeContainer 'hc-portal' } 'Gagal memeriksa'
    $script:EngineFailure = $false
    $script:Calls.Clear()
    Start-OfficeStack -Recreate
    Assert-True (@($script:Calls | Where-Object { $_[0] -eq 'volume' -and $_[1] -eq 'rm' }).Count -eq 0) 'recreate must preserve all volumes'
    Assert-True (@($script:Calls | Where-Object { $_[0] -eq 'rm' -and $_[1] -eq 'hc-portal' }).Count -eq 1) 'owned portal is recreated'
    Write-Host 'PASS: config, provider, isolation, ports, persistence, ownership, preflight and recreate.'
} finally {
    # Only known fixtures are removed. No recursive deletion or user data touched.
    Remove-Item -LiteralPath $configPath -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $script:TestRoot 'certs\corp-root-ca.pem') -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $script:TestRoot 'certs') -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $script:TestRoot -ErrorAction SilentlyContinue
}
