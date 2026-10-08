# Sekali saja, setelah `podman machine init`: memasang layanan kecil di mesin
# Podman yang menjaga MTU eth0 tetap 1280.
#
# Kenapa: di sebagian jaringan (VPN, Wi-Fi kantor/rumah tertentu) paket besar
# dibuang diam-diam. Koneksi TCP tersambung, tetapi jabat tangan TLS macet:
# gejalanya "TLS handshake timeout" saat podman pull/load, dan "SMTP tidak
# dapat dihubungi: The handshake operation timed out" di halaman Status
# sistem. 1280 adalah ukuran minimum IPv6, jadi aman di jaringan mana pun;
# di jaringan yang sehat tidak ada bedanya.
#
# Layanannya menjaga, bukan sekali pasang: setelah mesin restart, jaringan WSL
# bisa mengembalikan MTU ke 1500 beberapa saat SETELAH layanan boot berjalan.
# Jadi ia memeriksa tiap 20 detik dan memasang ulang bila berubah.
#
# Jalankan dari akun Windows pemilik mesin Podman:
#   powershell -ExecutionPolicy Bypass -File .\deploy\windows\perbaiki-mtu.ps1

[CmdletBinding()]
param([int]$Mtu = 1280)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "podman-common.ps1")

$unit = @"
[Unit]
Description=Keep eth0 MTU at $Mtu so TLS (SMTP, LDAPS, image pulls) does not stall on this network
After=network.target

[Service]
Type=simple
ExecStart=/bin/sh -c 'while true; do ip link show eth0 2>/dev/null | grep -q "mtu $Mtu " || ip link set dev eth0 mtu $Mtu 2>/dev/null; sleep 20; done'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"@

Wait-PodmanReady
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($unit.Replace("`r", "")))
$script = "echo $encoded | base64 -d | sudo tee /etc/systemd/system/hc-mtu.service >/dev/null && " +
          "sudo systemctl daemon-reload && sudo systemctl enable hc-mtu.service >/dev/null 2>&1 && " +
          "sudo systemctl restart hc-mtu.service && sleep 2 && ip link show eth0"
$result = Invoke-Podman -ArgumentList @("machine", "ssh", $script) -What "Memasang perbaikan MTU"
$line = $result.Output | Where-Object { $_ -match "mtu" } | Select-Object -First 1
if ($line -notmatch "mtu $Mtu ") {
    throw "MTU belum berubah ($line). Jalankan berkas ini sekali lagi."
}
Write-Host "Selesai. MTU eth0 sekarang $Mtu dan dijaga setelah restart."
