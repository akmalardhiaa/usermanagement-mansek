[CmdletBinding()]
param([string]$BackupPath)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'office-runtime.ps1')
Wait-OfficePodman
if (-not (Test-OfficeContainer 'hc-portal')) { throw 'Portal belum dibuat; tidak ada backup aplikasi yang bisa dibuat.' }
if (-not $BackupPath) { $BackupPath = Join-Path $script:OfficeRoot 'backups' }
$null = New-Item -ItemType Directory -Force -Path $BackupPath
$archive = Join-Path ((Resolve-Path -LiteralPath $BackupPath).Path) ('hc-portal-data-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.tar')
$state = Invoke-Podman -ArgumentList @('inspect', '--format', '{{.State.Running}}', 'hc-portal') -What 'Membaca status portal'
$wasRunning = ($state.Output -join '').Trim() -eq 'true'
try {
    if ($wasRunning) { $null = Invoke-Podman -ArgumentList @('stop', '--time', '30', 'hc-portal') -What 'Menghentikan portal untuk backup konsisten' }
    $null = Invoke-Podman -ArgumentList @('volume', 'export', 'hc-portal-data', '--output', $archive) -What 'Mengekspor data'
    if (-not (Test-Path -LiteralPath $archive) -or (Get-Item -LiteralPath $archive).Length -eq 0) { throw 'Arsip backup tidak terbentuk.' }
    Write-Host "Backup: $archive"
    Write-Host 'Simpan juga .env.production.local dan CA di tempat rahasia yang disetujui IT. Retensi diatur IT.'
} finally {
    if ($wasRunning) { $null = Invoke-Podman -ArgumentList @('start', 'hc-portal') -What 'Menyalakan kembali portal' }
}
