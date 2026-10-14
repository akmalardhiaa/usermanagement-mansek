[CmdletBinding()]
param([string]$Destination)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator

if (-not $Destination) {
    $Destination = Join-Path $script:Root ('backups\hc-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
}
$Destination = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $Destination) { throw 'Folder tujuan backup sudah ada; gunakan nama baru.' }
foreach ($name in @('data','config','certificates')) {
    $source = [IO.Path]::GetFullPath((Join-Path $script:Root $name))
    if ($Destination.StartsWith(($source.TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Backup tidak boleh disimpan di dalam data, config, atau certificates.'
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $script:Root 'data') -PathType Container)) {
    throw 'Folder data belum ada.'
}
$portal = Get-Service -Name 'HC-Portal' -ErrorAction SilentlyContinue
$wasRunning = $portal -and $portal.Status -eq 'Running'
try {
    if ($wasRunning) {
        Stop-Service -Name 'HC-Portal' -ErrorAction Stop
        (Get-Service 'HC-Portal').WaitForStatus('Stopped', (New-TimeSpan -Seconds 45))
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($name in @('data','config','certificates')) {
        $source = Join-Path $script:Root $name
        if (-not (Test-Path -LiteralPath $source -PathType Container)) { continue }
        $target = Join-Path $Destination $name
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        & robocopy.exe $source $target /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XJ /NFL /NDL /NJH /NJS | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Backup $name gagal (robocopy $LASTEXITCODE)." }
    }
    $files = @(Get-ChildItem -LiteralPath $Destination -Recurse -File -Force | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($Destination.Length + 1).Replace('\', '/')
            length = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    })
    $manifest = [ordered]@{ createdUtc = [DateTime]::UtcNow.ToString('o'); files = $files }
    [IO.File]::WriteAllText((Join-Path $Destination 'manifest.json'), ($manifest | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
    Write-Host "Backup konsisten: $Destination"
} finally {
    if ($wasRunning) { Start-Service -Name 'HC-Portal' -ErrorAction SilentlyContinue }
}
