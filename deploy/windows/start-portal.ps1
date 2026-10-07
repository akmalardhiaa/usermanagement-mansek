[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "podman-common.ps1")

$appRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
# Beside the application rather than a fixed C:\hc-portal, so a portal
# installed somewhere else still logs into its own folder.
$logRoot = Join-Path $appRoot "logs"
$logFile = Join-Path $logRoot "start-portal.log"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Write-PortalLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logFile -Value $line
    Write-Host $line
}

$log = { param($line) Write-PortalLog $line }

Push-Location $appRoot
try {
    Write-PortalLog "Memulai Podman machine (jika belum berjalan)."
    Wait-PodmanReady -OnLine $log

    Write-PortalLog "Menjalankan compose production."
    $null = Invoke-Podman -ArgumentList @("compose", "-f", "compose.production.yml", "up", "-d") `
        -What "podman compose up" -OnLine $log
    Write-PortalLog "Portal diminta berjalan."
} catch {
    Write-PortalLog "GAGAL: $($_.Exception.Message)"
    throw
} finally {
    Pop-Location
}
