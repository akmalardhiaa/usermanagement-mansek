[CmdletBinding()]
param([Parameter(Mandatory)][string]$DnsName,[Parameter(Mandatory)][string]$CertificateThumbprint,[string]$SiteName='HCUserManagement')
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
if ($DnsName -notmatch '^[A-Za-z0-9.-]+$' -or $SiteName -notmatch '^[A-Za-z0-9_-]+$') { throw 'Nama DNS atau site IIS tidak valid.' }
$CertificateThumbprint = ($CertificateThumbprint -replace '\s','').ToUpperInvariant()
if ($CertificateThumbprint -notmatch '^[0-9A-F]{40}$') { throw 'Thumbprint sertifikat harus SHA-1 sepanjang 40 digit heksadesimal.' }
$configValues = Read-ProductionEnv
Assert-ProductionEnv $configValues
if (([uri]$configValues.APP_BASE_URL).DnsSafeHost -ine $DnsName) { throw 'DnsName tidak sama dengan APP_BASE_URL dalam production.env.' }
Import-Module WebAdministration -ErrorAction Stop
foreach ($feature in @('Web-Server')) {
    if (-not (Get-WindowsFeature $feature).Installed) { throw "Fitur IIS $feature belum terpasang; minta tim IT memasangnya." }
}
$appcmd = Join-Path $env:windir 'System32\inetsrv\appcmd.exe'
foreach ($section in @('system.webServer/rewrite','system.webServer/proxy')) {
    & $appcmd list config "-section:$section" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Modul IIS $section belum terpasang." }
}
$cert = Get-Item "Cert:\LocalMachine\My\$CertificateThumbprint" -ErrorAction Stop
if ($cert.NotAfter -le (Get-Date) -or -not $cert.HasPrivateKey) { throw 'Sertifikat HTTPS kedaluwarsa atau private key tidak tersedia.' }
if ($cert.NotBefore -gt (Get-Date)) { throw 'Sertifikat HTTPS belum berlaku.' }
$dnsValid = $false
foreach ($entry in $cert.DnsNameList) {
    if ($entry.Unicode -ieq $DnsName) { $dnsValid = $true; break }
}
if (-not $dnsValid) { throw 'DNS portal tidak tercantum pada Subject Alternative Name sertifikat.' }
$siteDir = Join-Path $script:Root 'iis'
New-Item -ItemType Directory -Force $siteDir | Out-Null
$existingSite = Get-Website -Name $SiteName -ErrorAction SilentlyContinue
if ($existingSite -and [IO.Path]::GetFullPath($existingSite.PhysicalPath).TrimEnd('\') -ine [IO.Path]::GetFullPath($siteDir).TrimEnd('\')) {
    throw "Site IIS $SiteName sudah ada dengan folder lain."
}
$config = @"
<?xml version="1.0" encoding="utf-8"?>
<configuration><system.webServer>
<rewrite><rules><rule name="Proxy to HC Portal" stopProcessing="true"><match url="(.*)" />
<serverVariables><set name="HTTP_X_FORWARDED_PROTO" value="https" /></serverVariables>
<action type="Rewrite" url="http://127.0.0.1:3000/{R:1}" appendQueryString="true" /></rule></rules></rewrite>
<httpProtocol><customHeaders><remove name="X-Powered-By" /><add name="Strict-Transport-Security" value="max-age=15552000" /><add name="X-Content-Type-Options" value="nosniff" /></customHeaders></httpProtocol>
<security><requestFiltering removeServerHeader="true" /></security>
</system.webServer></configuration>
"@
[IO.File]::WriteAllText((Join-Path $siteDir 'web.config'), $config, (New-Object Text.UTF8Encoding($false)))
& $appcmd set config -section:system.webServer/proxy /enabled:true /preserveHostHeader:true /commit:apphost
if ($LASTEXITCODE -ne 0) { throw 'ARR proxy gagal diaktifkan.' }
& $appcmd set config -section:system.webServer/rewrite/allowedServerVariables /+"[name='HTTP_X_FORWARDED_PROTO']" /commit:apphost
if ($LASTEXITCODE -ne 0) { throw 'IIS tidak mengizinkan X-Forwarded-Proto.' }
if (-not (Test-Path "IIS:\Sites\$SiteName")) { New-Website -Name $SiteName -PhysicalPath $siteDir -Port 443 -HostHeader $DnsName -Ssl -SslFlags 1 | Out-Null }
$binding = Get-WebBinding -Name $SiteName -Protocol https | Where-Object { $_.bindingInformation -like "*:443:$DnsName" }
if (-not $binding) { New-WebBinding -Name $SiteName -Protocol https -Port 443 -HostHeader $DnsName -SslFlags 1 | Out-Null; $binding = Get-WebBinding -Name $SiteName -Protocol https | Where-Object { $_.bindingInformation -like "*:443:$DnsName" } }
if (-not $binding -or [int]$binding.sslFlags -ne 1) { throw 'Binding HTTPS tidak memakai SNI. Periksa konflik binding 443 di IIS.' }
$binding.AddSslCertificate($CertificateThumbprint,'My')
Write-Host "IIS $SiteName siap untuk https://$DnsName. Uji dari klien dan periksa binding sertifikat."
