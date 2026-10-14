[CmdletBinding()]
param([switch]$Stop)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'office-runtime.ps1')
Wait-OfficePodman
foreach ($name in @('hc-portal', 'hc-ad-service')) {
    if (Test-OfficeContainer $name) {
        if ($Stop) {
            $null = Invoke-Podman -ArgumentList @('stop', '--time', '30', $name) -What "Menghentikan $name"
        } else {
            Write-Host "Container: $name"
            $null = Invoke-Podman -ArgumentList @('logs', '--tail', '40', $name) -What "Log $name" -OnLine { param($line) Write-Host $line }
        }
    }
}
if ($Stop) { Write-Host 'Container berhenti. Volume data dan mesin Hyper-V tetap ada.' }
else { Write-Host 'Sebelum membagikan log, periksa dan samarkan data pribadi atau rahasia.' }
