[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ImageArchive)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'office-runtime.ps1')
$archive = (Resolve-Path -LiteralPath $ImageArchive).Path
$checksum = Join-Path (Split-Path $archive) 'hc-images.sha256.txt'
if (-not (Test-Path -LiteralPath $checksum)) { throw 'Checksum hc-images.sha256.txt wajib berada di sebelah arsip.' }
$expected = ((Get-Content -LiteralPath $checksum -Raw).Trim() -split '\s+')[0]
if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'Checksum arsip tidak cocok.'
}
Wait-OfficePodman
Assert-OfficeConfig
& (Join-Path $PSScriptRoot 'backup-data.ps1')
$null = Invoke-Podman -ArgumentList @('load', '--input', $archive) -What 'Memuat versi baru'
Start-OfficeStack -Recreate
