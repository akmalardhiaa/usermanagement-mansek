[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
# Explicit operator restart applies .env/images changes. Named volumes remain.
& (Join-Path $PSScriptRoot 'start-portal.ps1') -Recreate
Write-Host 'Buka APP_BASE_URL melalui HTTPS IIS. /healthz lokal bukan tes koneksi AD.'
