[CmdletBinding()]
param([switch]$NoEditor)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'office-runtime.ps1')
Use-OfficePodman
if (-not (Get-OfficeMachine)) {
    Write-Host 'Membuat hc-office dengan Hyper-V: 2 vCPU, RAM 4 GiB, disk 40 GiB.'
    Write-Host 'Image OS Podman memerlukan akses registry/proxy atau cache offline dari IT.'
    $null = Invoke-Podman -ArgumentList @('machine', 'init', '--provider', 'hyperv', '--cpus', '2',
        '--memory', '4096', '--disk-size', '40', $script:OfficeMachine) -What 'Membuat mesin Hyper-V'
}
Wait-OfficePodman
$archive = Join-Path $script:OfficeRoot 'hc-images.tar'
$checksum = Join-Path $script:OfficeRoot 'hc-images.sha256.txt'
if (-not (Test-Path -LiteralPath $archive) -or -not (Test-Path -LiteralPath $checksum)) {
    throw 'Paket baru memerlukan hc-images.tar dan hc-images.sha256.txt. Buat lewat deploy/windows/package-office.ps1 di laptop.'
}
$expected = ((Get-Content -LiteralPath $checksum -Raw).Trim() -split '\s+')[0]
if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'SHA-256 arsip image tidak cocok. Kirim ulang paket.'
}
$null = Invoke-Podman -ArgumentList @('load', '--input', $archive) -What 'Memuat dua image' -OnLine { param($line) Write-Host $line }
$envFile = Join-Path $script:OfficeRoot '.env.production.local'
if (-not (Test-Path -LiteralPath $envFile)) {
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] 32
        $generator.GetBytes($bytes); $token = [Convert]::ToBase64String($bytes)
        $generator.GetBytes($bytes); $key = [Convert]::ToBase64String($bytes)
    } finally { $generator.Dispose() }
    $template = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'env.production.example')
    $template = $template -replace '(?m)^AD_SERVICE_TOKEN=.*$', "AD_SERVICE_TOKEN=$token"
    $template = $template -replace '(?m)^OUTBOX_ENCRYPTION_KEY=.*$', "OUTBOX_ENCRYPTION_KEY=$key"
    [IO.File]::WriteAllText($envFile, $template, (New-Object Text.UTF8Encoding($false)))
    Write-Host '.env.production.local dibuat dengan kunci acak. Batasi ACL ke operator dan administrator.'
} else { Write-Host 'Pengaturan lama dipertahankan.' }
$null = New-Item -ItemType Directory -Force -Path (Join-Path $script:OfficeRoot 'certs')
Write-Host 'Isi pengaturan bersama IT; simpan CA publik sebagai certs\corp-root-ca.pem.'
Write-Host 'Baca PANDUAN-PC-KANTOR.md untuk IIS, AD, OU, password awal, dan uji penerimaan.'
Write-Host 'Setelah lengkap, jalankan deploy/windows/kantor-jalankan.ps1. Tidak ada MTU yang diubah otomatis.'
if (-not $NoEditor) { Write-Host "Buka dengan editor: $envFile" }
