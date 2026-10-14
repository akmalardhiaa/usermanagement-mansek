[CmdletBinding()]
param([string]$InstallDir = $PSScriptRoot)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
Assert-ProductionEnv (Read-ProductionEnv)

$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$winsw = Join-Path $InstallDir 'WinSW.exe'
foreach ($file in @('WinSW.exe','portal-service.xml','ad-service.xml','start-portal.ps1',
                   'start-ad-service.ps1','runtime\python\python.exe','runtime\java\bin\java.exe',
                   'ad-service.jar','portal\app\main.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $file) -PathType Leaf)) {
        throw "Paket instalasi tidak lengkap: $file"
    }
}
foreach ($item in @(@('ad-service','HC-AD-Service'),@('portal-service','HC-Portal'))) {
    $name, $serviceName = $item
    $exe = Join-Path $InstallDir "$name.exe"
    $existing = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
    if ($existing) {
        Assert-ServiceOwned $existing $InstallDir "$name.exe"
        if ($existing.State -eq 'Running') {
            Stop-Service -Name $serviceName -ErrorAction Stop
            (Get-Service $serviceName).WaitForStatus('Stopped', (New-TimeSpan -Seconds 45))
        }
    }
    Copy-Item -LiteralPath $winsw -Destination $exe -Force
    if ($existing) { & $exe refresh } else { & $exe install }
    if ($LASTEXITCODE -ne 0) { throw "Registrasi $serviceName gagal (WinSW $LASTEXITCODE)." }
}

foreach ($name in @('HC-AD-Service','HC-Portal')) {
    Start-Service -Name $name -ErrorAction Stop
    (Get-Service $name).WaitForStatus('Running', (New-TimeSpan -Seconds 45))
}
$ok = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    try {
        & (Join-Path $InstallDir 'health-check.ps1')
        $ok = $true
        break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ok) {
    Stop-Service -Name 'HC-Portal' -ErrorAction SilentlyContinue
    throw 'Service terdaftar tetapi health check gagal. Periksa log di ProgramData\HCUserManagement\logs.'
}
