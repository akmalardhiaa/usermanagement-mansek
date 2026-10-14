[CmdletBinding()]
param([Parameter(Mandatory)][string]$InstallDir)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$running = @()
try {
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.ProductType -ne 3 -or [int]$os.BuildNumber -lt 20348 -or -not [Environment]::Is64BitOperatingSystem) {
        throw 'Diperlukan Windows Server 2022/2025 x64 pada member server, bukan domain controller.'
    }
    Import-Module ServerManager -ErrorAction Stop
    if (-not (Get-WindowsFeature Web-Server).Installed) { throw 'IIS Web-Server belum terpasang.' }
    $appcmd = Join-Path $env:windir 'System32\inetsrv\appcmd.exe'
    if (-not (Test-Path -LiteralPath $appcmd)) { throw 'appcmd.exe IIS tidak tersedia.' }
    & $appcmd list config -section:system.webServer/rewrite 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'IIS URL Rewrite belum terpasang.' }
    & $appcmd list config -section:system.webServer/proxy 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'IIS Application Request Routing belum terpasang.' }
    $drive = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($InstallDir)).TrimEnd('\')
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$drive'"
    if (-not $disk -or $disk.FreeSpace -lt 2GB) { throw 'Ruang kosong minimal 2 GB diperlukan untuk instalasi dan upgrade.' }
    if (Test-Path -LiteralPath $script:Config -PathType Leaf) {
        Assert-ProductionEnv (Read-ProductionEnv)
    }
    $existing = @()
    foreach ($name in @('HC-Portal','HC-AD-Service')) {
        $service = Get-CimInstance Win32_Service -Filter "Name='$name'" -ErrorAction SilentlyContinue
        if (-not $service) { continue }
        $exeName = if ($name -eq 'HC-Portal') { 'portal-service.exe' } else { 'ad-service.exe' }
        Assert-ServiceOwned $service $InstallDir $exeName
        $existing += $name
        if ($service.State -eq 'Running') { $running += $name }
    }
    foreach ($name in @('HC-Portal','HC-AD-Service')) {
        if ($name -in $running) {
            Stop-Service -Name $name -ErrorAction Stop
            (Get-Service $name).WaitForStatus('Stopped', (New-TimeSpan -Seconds 45))
        }
    }
    $occupied = @(Get-NetTCPConnection -State Listen -LocalPort 3000,8081 -ErrorAction SilentlyContinue)
    if ($occupied.Count) { throw 'Port 3000 atau 8081 masih dipakai proses lain. Hentikan proses tersebut sebelum instalasi.' }
    if ($existing.Count -and (Test-Path -LiteralPath (Join-Path $script:Root 'data') -PathType Container)) {
        & (Join-Path $PSScriptRoot 'backup-data.ps1')
    }
    Write-Host 'Pemeriksaan server selesai. File boleh dipasang.'
} catch {
    $message = "$(Get-Date -Format o) preflight: $($_.Exception.Message)"
    try { [IO.File]::AppendAllText((Join-Path $env:TEMP 'HCUserManagement-preflight.log'), ($message + [Environment]::NewLine), (New-Object Text.UTF8Encoding($false))) } catch {}
    foreach ($name in @('HC-AD-Service','HC-Portal')) {
        if ($name -in $running) { Start-Service -Name $name -ErrorAction SilentlyContinue }
    }
    Write-Error $_.Exception.Message
    exit 1
}
