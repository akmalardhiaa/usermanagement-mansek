[CmdletBinding()]
param([string]$InstallDir = $PSScriptRoot)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')

foreach ($item in @(@('HC-Portal','portal-service'),@('HC-AD-Service','ad-service'))) {
    $serviceName, $baseName = $item
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
    if (-not $service) { continue }
    $exe = Join-Path $InstallDir "$baseName.exe"
    Assert-ServiceOwned $service $InstallDir "$baseName.exe"
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "WinSW tidak tersedia untuk ${serviceName}: $exe" }
    if ($service.State -ne 'Stopped') {
        Stop-Service -Name $serviceName -ErrorAction Stop
        (Get-Service $serviceName).WaitForStatus('Stopped', (New-TimeSpan -Seconds 45))
    }
    & $exe uninstall
    if ($LASTEXITCODE -ne 0) { throw "Gagal menghapus service $serviceName (WinSW $LASTEXITCODE)." }
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        if (-not (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Seconds 1
    }
    if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) { throw "Service $serviceName belum terhapus." }
}
Write-Host 'Service terhapus. Data dan konfigurasi di ProgramData tetap tersimpan.'
