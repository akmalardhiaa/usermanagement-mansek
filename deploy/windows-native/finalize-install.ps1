[CmdletBinding()]
param([Parameter(Mandatory)][string]$InstallDir)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator

function Invoke-Icacls([string]$Path, [string[]]$Arguments) {
    & (Join-Path $env:windir 'System32\icacls.exe') $Path @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Gagal membatasi ACL $Path (icacls $LASTEXITCODE)." }
}

try {
    foreach ($name in @('data','config','certificates','logs','backups','iis')) {
        New-Item -ItemType Directory -Force (Join-Path $script:Root $name) | Out-Null
    }
    Invoke-Icacls $script:Root @('/grant:r','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F','*S-1-5-19:(OI)(CI)RX','/T')
    Invoke-Icacls $script:Root @('/inheritance:r','/T')
    foreach ($name in @('data','logs')) {
        Invoke-Icacls (Join-Path $script:Root $name) @('/grant:r','*S-1-5-19:(OI)(CI)M','/T')
    }
    Invoke-Icacls (Join-Path $script:Root 'backups') @('/remove:g','*S-1-5-19','/T')
    if (-not (Test-Path -LiteralPath $script:Config -PathType Leaf)) {
        Write-Host "File aplikasi terpasang. Isi $script:Config lalu jalankan finalize-install.ps1 sebagai Administrator."
        exit 0
    }
    Assert-ProductionEnv (Read-ProductionEnv)
    & (Join-Path $InstallDir 'install-services.ps1') -InstallDir $InstallDir
    if (-not (Get-Service 'HC-Portal' -ErrorAction SilentlyContinue)) { throw 'HC-Portal belum terdaftar.' }
    & (Join-Path $InstallDir 'health-check.ps1')
    Write-Host 'Instalasi service selesai dan health lokal lulus.'
} catch {
    $message = "$(Get-Date -Format o) finalize: $($_.Exception.Message)"
    try {
        New-Item -ItemType Directory -Force (Join-Path $script:Root 'logs') | Out-Null
        [IO.File]::AppendAllText((Join-Path $script:Root 'logs\install-errors.log'), ($message + [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))
    } catch {}
    Write-Error $_.Exception.Message
    exit 1
}
