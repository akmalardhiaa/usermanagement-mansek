[CmdletBinding()]
param(
    # Default: "backups" beside the application. Point it at another disk or a
    # share for a copy that survives the loss of this server.
    [string]$BackupPath
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "podman-common.ps1")

$appRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $BackupPath) { $BackupPath = Join-Path $appRoot "backups" }
New-Item -ItemType Directory -Path $BackupPath -Force | Out-Null

Wait-PodmanReady

Push-Location $appRoot
$stopped = $false
try {
    # Stopped so the export is one consistent moment, not a store caught
    # halfway through a write.
    $null = Invoke-Podman -ArgumentList @("compose", "-f", "compose.production.yml", "stop", "portal") `
        -What "Menghentikan portal untuk backup yang konsisten"
    $stopped = $true

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $archive = Join-Path $BackupPath "hc-portal-data-$timestamp.tar"
    $null = Invoke-Podman -ArgumentList @("volume", "export", "hc-portal-data", "--output", $archive) `
        -What "podman volume export"

    # Old archives are removed only after a new one exists, so a run that
    # fails never leaves fewer backups than it found.
    $cutoff = (Get-Date).AddDays(-30)
    Get-ChildItem -LiteralPath $BackupPath -Filter "hc-portal-data-*.tar" -File |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        Remove-Item -Force
    Write-Host "Backup volume hc-portal-data dibuat: $archive"
} finally {
    if ($stopped) {
        $restart = Invoke-Native -FilePath "podman" -ArgumentList @("compose", "-f", "compose.production.yml", "up", "-d")
        if ($restart.ExitCode -ne 0) {
            Write-Warning "Backup selesai tetapi portal gagal dimulai kembali (kode $($restart.ExitCode)). Jalankan start-portal.ps1."
        }
    }
    Pop-Location
}

# Contoh pendaftaran harian Task Scheduler (jalankan sebagai Administrator):
# $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\hc-portal\deploy\windows\backup-data.ps1"'
# $trigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
# $credential = Get-Credential -UserName "DOMAIN\akun-pemilik-podman"
# Register-ScheduledTask -TaskName "HC Portal data backup" -Action $action -Trigger $trigger -RunLevel Highest -User $credential.UserName -Password $credential.GetNetworkCredential().Password
