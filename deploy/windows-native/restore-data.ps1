[CmdletBinding()]
param([Parameter(Mandatory)][string]$BackupDirectory)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator

$BackupDirectory = [IO.Path]::GetFullPath($BackupDirectory)
foreach ($name in @('data','config','certificates')) {
    $livePath = [IO.Path]::GetFullPath((Join-Path $script:Root $name)).TrimEnd('\')
    if ($BackupDirectory.TrimEnd('\').Equals($livePath, [StringComparison]::OrdinalIgnoreCase) -or
        $BackupDirectory.StartsWith($livePath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Folder backup tidak boleh berada di dalam data, config, atau certificates aktif.'
    }
}
$manifestFile = Join-Path $BackupDirectory 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestFile -PathType Leaf)) { throw 'Backup tidak memiliki manifest.json.' }
$manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $manifest.files) { throw 'Manifest backup rusak.' }
foreach ($file in $manifest.files) {
    $relative = [string]$file.path
    if ($relative -notmatch '^(data|config|certificates)/' -or $relative.Contains('..') -or [IO.Path]::IsPathRooted($relative)) {
        throw 'Manifest backup memiliki path tidak aman.'
    }
    $path = Join-Path $BackupDirectory $relative.Replace('/', '\')
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or
        (Get-Item -LiteralPath $path).Length -ne [long]$file.length -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne [string]$file.sha256) {
        throw "Backup rusak atau berubah: $relative"
    }
}
$rescue = Join-Path $script:Root ('backups\pre-restore-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
& (Join-Path $PSScriptRoot 'backup-data.ps1') -Destination $rescue
$services = @('HC-Portal','HC-AD-Service')
$runningBeforeRestore = @($services | Where-Object { (Get-Service -Name $_ -ErrorAction SilentlyContinue).Status -eq 'Running' })
try {
    foreach ($name in $services) {
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($service -and $service.Status -eq 'Running') {
            Stop-Service -Name $name -ErrorAction Stop
            (Get-Service $name).WaitForStatus('Stopped', (New-TimeSpan -Seconds 45))
        }
    }
    foreach ($name in @('data','config','certificates')) {
        $source = Join-Path $BackupDirectory $name
        if (-not (Test-Path -LiteralPath $source -PathType Container)) { continue }
        $target = Join-Path $script:Root $name
        New-Item -ItemType Directory -Force $target | Out-Null
        & robocopy.exe $source $target /MIR /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XJ /NFL /NDL /NJH /NJS | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Restore $name gagal (robocopy $LASTEXITCODE)." }
    }
    foreach ($file in $manifest.files) {
        $target = Join-Path $script:Root ([string]$file.path).Replace('/', '\')
        if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or
            (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -cne [string]$file.sha256) {
            throw "Verifikasi restore gagal: $($file.path)"
        }
    }
    Write-Host "Restore terverifikasi. Backup sebelum restore: $rescue"
} catch {
    $failure = $_
    $rollbackErrors = @()
    foreach ($name in @('data','config','certificates')) {
        $source = Join-Path $rescue $name
        if (Test-Path -LiteralPath $source -PathType Container) {
            & robocopy.exe $source (Join-Path $script:Root $name) /MIR /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XJ /NFL /NDL /NJH /NJS | Out-Null
            if ($LASTEXITCODE -ge 8) { $rollbackErrors += "$name (robocopy $LASTEXITCODE)" }
        }
    }
    if ($rollbackErrors.Count) { throw "Restore gagal dan rollback juga gagal: $($rollbackErrors -join ', '). Backup sebelum restore: $rescue. Masalah awal: $($failure.Exception.Message)" }
    throw "Restore gagal; salinan sebelum restore dikembalikan dari $rescue. $($failure.Exception.Message)"
} finally {
    foreach ($name in @('HC-AD-Service','HC-Portal')) {
        if ($name -in $runningBeforeRestore) { Start-Service -Name $name -ErrorAction SilentlyContinue }
    }
}
if ($runningBeforeRestore.Count -eq 2) { & (Join-Path $PSScriptRoot 'health-check.ps1') }
