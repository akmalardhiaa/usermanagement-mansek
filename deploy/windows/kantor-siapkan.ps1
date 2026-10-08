# KANTOR-2-siapkan.bat: everything between "Podman is installed" and "fill in
# the settings with IT": the Podman machine, the network fix, the two images,
# and .env.production.local with its two secrets already made. Safe to run
# again: each step skips what is already done.

[CmdletBinding()]
param([switch]$NoEditor)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "kantor-common.ps1")
Set-Location $script:AppRoot

try {
    Use-PodmanPath

    Write-Step "[1/5] Mesin Podman"
    $machines = Invoke-Native -FilePath "podman" -ArgumentList @("machine", "list", "--format", "{{.Name}}")
    if (-not ($machines.Output | Where-Object { $_ -match "\S" })) {
        Write-Host "   Membuat mesin Podman (sekali saja, beberapa menit, perlu internet)..."
        $null = Invoke-Podman -ArgumentList @("machine", "init", "--memory", "4096") -What "podman machine init" -OnLine { param($l) Write-Host "   $l" }
    }
    Wait-PodmanReady
    Write-Ok "mesin Podman menyala"

    Write-Step "[2/5] Perbaikan jaringan (MTU)"
    & (Join-Path $PSScriptRoot "perbaiki-mtu.ps1") | Out-Null
    Write-Ok "MTU 1280 dipasang, tetap berlaku setelah restart"

    Write-Step "[3/5] Memuat image portal dan layanan AD"
    $archive = @("hc-images.tar.gz", "..\hc-images.tar.gz") | ForEach-Object { Join-Path $script:AppRoot $_ } |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $archive) {
        throw "hc-images.tar.gz tidak ditemukan di $($script:AppRoot). Taruh file itu di folder ini, lalu jalankan lagi."
    }
    $shaFile = Join-Path (Split-Path $archive) "hc-images.sha256.txt"
    if (Test-Path -LiteralPath $shaFile) {
        Write-Host "   Memeriksa keutuhan file (sidik jari SHA-256)..."
        $expected = ((Get-Content -LiteralPath $shaFile -Raw).Trim() -split "\s+")[0].ToLower()
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower()
        if ($expected -ne $actual) {
            throw "hc-images.tar.gz rusak atau tidak lengkap (sidik jari tidak cocok). Kirim ulang file itu."
        }
        Write-Ok "file utuh"
    }
    $null = Invoke-Podman -ArgumentList @("load", "-i", $archive) -What "podman load" -OnLine { param($l) if ($l -match "Loaded image") { Write-Host "   $l" } }
    Write-Ok "image dimuat"

    Write-Step "[4/5] Berkas pengaturan .env.production.local"
    $envFile = Join-Path $script:AppRoot ".env.production.local"
    if (-not (Test-Path -LiteralPath $envFile)) {
        $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        $bytes = New-Object byte[] 32
        $generator.GetBytes($bytes); $token = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
        $generator.GetBytes($bytes); $key = [Convert]::ToBase64String($bytes)
        $template = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "env.production.example")
        $text = $template -replace '(?m)^AD_SERVICE_TOKEN=.*$', "AD_SERVICE_TOKEN=$token" `
                          -replace '(?m)^OUTBOX_ENCRYPTION_KEY=.*$', "OUTBOX_ENCRYPTION_KEY=$key" `
                          -replace '(?m)^APP_BASE_URL=.*$', "APP_BASE_URL=http://localhost:3000" `
                          -replace '(?m)^NEXT_PUBLIC_BRAND_NAME=<.*$', "NEXT_PUBLIC_BRAND_NAME=Mandiri Sekuritas"
        [IO.File]::WriteAllText($envFile, $text)
        Write-Ok "dibuat, dengan dua kunci rahasia acak yang sudah terisi"
        Write-Host "   Simpan salinan berkas ini di tempat aman yang disetujui kantor." -ForegroundColor Cyan
    } else {
        Write-Ok "sudah ada, tidak diubah"
    }
    $unfilled = Get-UnfilledSettings $envFile
    if ($unfilled.Count) {
        Write-Todo "masih perlu diisi bersama IT: $($unfilled -join ', ')"
        if (-not $NoEditor) { Start-Process notepad.exe $envFile }
    } else {
        Write-Ok "semua nilai sudah diisi"
    }

    Write-Step "[5/5] Sertifikat CA perusahaan"
    $ca = Join-Path $script:AppRoot "certs\corp-root-ca.pem"
    New-Item -ItemType Directory -Force -Path (Split-Path $ca) | Out-Null
    if (Test-Path -LiteralPath $ca) {
        if ((Get-Content -LiteralPath $ca -TotalCount 1) -match "BEGIN CERTIFICATE") { Write-Ok "certs\corp-root-ca.pem ada" }
        else { Write-Todo "certs\corp-root-ca.pem bukan format PEM (harus diawali -----BEGIN CERTIFICATE-----)" }
    } else {
        Write-Todo "simpan sertifikat CA dari IT sebagai certs\corp-root-ca.pem (folder certs sudah dibuat)"
        if (-not $NoEditor) { Start-Process explorer.exe (Split-Path $ca) }
    }

    Write-Host ""
    Write-Host " Selesai. Setelah semua tanda !! beres, klik dua kali KANTOR-3-jalankan.bat" -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Todo $_.Exception.Message
    exit 1
}
