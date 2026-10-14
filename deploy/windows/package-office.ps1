[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Destination)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'podman-common.ps1')
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
# Use the laptop's existing Podman connection. Never force the office provider here.
$null = Invoke-Podman -ArgumentList @('info') -What 'Podman laptop harus sudah berjalan'
foreach ($spec in @(@('portal', 'localhost/hc-portal:latest'), @('ad-service', 'localhost/hc-ad-service:latest'))) {
    $null = Invoke-Podman -ArgumentList @('build', '--layers', '--platform', 'linux/amd64', '-t', $spec[1], (Join-Path $projectRoot $spec[0])) `
        -What "Build $($spec[0])" -OnLine { param($line) Write-Host $line }
}
$null = New-Item -ItemType Directory -Force -Path $Destination
$destinationRoot = (Resolve-Path -LiteralPath $Destination).Path
# Always a fresh staging directory. Never mirror/delete an existing destination.
$bundleRoot = Join-Path $destinationRoot ('hc-package-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
$appRoot = Join-Path $bundleRoot 'hc-portal'
$windowsRoot = Join-Path $appRoot 'deploy\windows'
$null = New-Item -ItemType Directory -Path $windowsRoot -Force
$null = New-Item -ItemType Directory -Path (Join-Path $appRoot 'certs')
# Explicit allowlist: no source checkout, .env, certificates, logs or user data.
foreach ($name in @('MULAI-DI-SINI.txt', 'PANDUAN-AD-OU.md')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $name) -Destination $appRoot
}
$null = New-Item -ItemType Directory -Path (Join-Path $appRoot 'deploy\linux') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'compose.production.yml') -Destination $appRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy\linux\README.md') -Destination (Join-Path $appRoot 'deploy\linux')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'env.production.example') -Destination $windowsRoot
$archive = Join-Path $appRoot 'hc-images.tar'
$null = Invoke-Podman -ArgumentList @('save', '--multi-image-archive', '--output', $archive,
    'localhost/hc-portal:latest', 'localhost/hc-ad-service:latest') -What 'Menyimpan image'
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $appRoot 'hc-images.sha256.txt'), "$hash  hc-images.tar`n", (New-Object Text.UTF8Encoding($false)))
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = Join-Path $bundleRoot 'hc-portal-kantor.zip'
[IO.Compression.ZipFile]::CreateFromDirectory($appRoot, $zip, [IO.Compression.CompressionLevel]::Optimal, $true)
Write-Host "Paket: $zip"
Write-Host 'Target paket: Linux server dengan Podman. Ikuti deploy/linux/README.md.'
