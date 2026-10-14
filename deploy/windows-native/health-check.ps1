[CmdletBinding()]
param([switch]$CheckAd)
$ErrorActionPreference = 'Stop'
foreach ($item in @(@('HC-Portal','http://127.0.0.1:3000/healthz'),@('HC-AD-Service','http://127.0.0.1:8081/v1/health'))) {
    $svc = Get-Service -Name $item[0] -ErrorAction Stop
    if ($svc.Status -ne 'Running') { throw "$($item[0]) tidak berjalan." }
    $result = Invoke-WebRequest -Uri $item[1] -UseBasicParsing -TimeoutSec 10
    if ($result.StatusCode -ne 200) { throw "$($item[0]) health check gagal." }
}
if ($CheckAd) {
    . (Join-Path $PSScriptRoot 'common.ps1')
    $config = Read-ProductionEnv
    $hostName = ([uri]$config.AD_LDAP_URL).Host
    $result = Test-NetConnection -ComputerName $hostName -Port 636 -WarningAction SilentlyContinue
    if (-not $result.TcpTestSucceeded) { throw 'Port LDAPS 636 tidak terjangkau.' }
    Write-Host 'TCP 636 terjangkau; validasi sertifikat dan bind terjadi saat aplikasi memakai LDAPS.'
}
Write-Host 'Layanan lokal sehat.'
