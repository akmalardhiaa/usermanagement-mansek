[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'common.ps1')
try {
    $values = Read-ProductionEnv
    Assert-ProductionEnv $values
    Write-Host 'production.env valid secara lokal. Koneksi AD, SMTP/Graph dan HTTPS masih perlu diuji.'
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
