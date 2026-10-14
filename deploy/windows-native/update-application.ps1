[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateScript({Test-Path -LiteralPath $_ -PathType Leaf})][string]$InstallerPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{64}$')][string]$ExpectedSha256
)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator

$InstallerPath = [IO.Path]::GetFullPath($InstallerPath)
if ([IO.Path]::GetFileName($InstallerPath) -notlike 'HCUserManagement-Setup*.exe') {
    throw 'Gunakan HCUserManagement-Setup.exe yang sudah diverifikasi.'
}
if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash -ine $ExpectedSha256) {
    throw 'SHA-256 installer tidak sama dengan nilai yang diharapkan.'
}
$log = Join-Path $script:Root ('logs\setup-update-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$process = Start-Process -FilePath $InstallerPath -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="' + $log + '"')) -Wait -PassThru -WindowStyle Hidden
if ($process.ExitCode -ne 0) { throw "Update gagal (Setup $($process.ExitCode)). Periksa $log; backup sebelum upgrade ada di ProgramData\HCUserManagement\backups." }
if (Test-Path -LiteralPath $script:Config -PathType Leaf) {
    & (Join-Path $PSScriptRoot 'health-check.ps1')
}
Write-Host "Update selesai. Log: $log"
