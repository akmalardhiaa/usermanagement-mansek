# KANTOR-3-jalankan.bat: checks the settings are complete, starts both
# containers (the same start-portal.ps1 the startup task uses), waits until
# the portal answers, and opens it in the browser.

[CmdletBinding()]
param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "kantor-common.ps1")
Set-Location $script:AppRoot

try {
    Use-PodmanPath

    Write-Step "[1/3] Memeriksa pengaturan"
    $envFile = Join-Path $script:AppRoot ".env.production.local"
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw ".env.production.local belum ada. Jalankan KANTOR-2-siapkan.bat dulu."
    }
    $unfilled = Get-UnfilledSettings $envFile
    if ($unfilled.Count) {
        if (-not $NoBrowser) { Start-Process notepad.exe $envFile }
        throw "Masih ada nilai <...> di .env.production.local: $($unfilled -join ', '). Isi bersama IT, simpan, lalu jalankan lagi."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $script:AppRoot "certs\corp-root-ca.pem"))) {
        throw "certs\corp-root-ca.pem belum ada. Simpan sertifikat CA dari IT di sana, lalu jalankan lagi."
    }
    Write-Ok "pengaturan lengkap"

    Write-Step "[2/3] Menyalakan portal dan layanan AD"
    # Quiet here (it says "already running" and the like); the whole story is in logs\start-portal.log.
    & (Join-Path $PSScriptRoot "start-portal.ps1") 6>$null | Out-Null
    Write-Ok "container diminta menyala"

    Write-Step "[3/3] Menunggu portal siap"
    $ready = $false
    foreach ($i in 1..45) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:3000/healthz"
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        Write-Todo "portal belum menjawab. Klik dua kali KANTOR-lihat-log.bat untuk melihat penyebabnya."
        exit 1
    }
    $ad = Invoke-Native -FilePath "podman" -ArgumentList @("inspect", "--format", "{{.State.Status}}", "hc-ad-service")
    if (($ad.Output -join "") -notmatch "running") {
        Write-Todo "layanan AD tidak menyala. Klik dua kali KANTOR-lihat-log.bat: pesannya menyebut pengaturan yang kurang."
        exit 1
    }
    Write-Ok "portal siap di http://localhost:3000"
    $base = (Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^APP_BASE_URL=' } | Select-Object -First 1) -replace '^APP_BASE_URL=', ''
    if ($base -and $base -ne "http://localhost:3000") { Write-Host "   Dari PC lain: $base" }
    if (-not $NoBrowser) { Start-Process "http://localhost:3000" }
    Write-Host ""
    Write-Host " Login dengan akun AD, pindai kode QR dengan HP, lalu buka menu Status sistem." -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Todo $_.Exception.Message
    exit 1
}
