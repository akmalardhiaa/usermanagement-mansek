# Sekali saja, setelah `podman machine init`: memasang layanan kecil di mesin
# Podman yang menurunkan MTU eth0 ke 1280 setiap kali mesin menyala.
#
# Kenapa: di sebagian jaringan (VPN, Wi-Fi kantor/rumah tertentu) paket besar
# dibuang diam-diam. Koneksi TCP tersambung, tetapi jabat tangan TLS macet:
# gejalanya "TLS handshake timeout" saat podman pull/load, dan "SMTP tidak
# dapat dihubungi: The handshake operation timed out" di halaman Status
# sistem. 1280 adalah ukuran minimum IPv6, jadi aman di jaringan mana pun;
# di jaringan yang sehat tidak ada bedanya.
#
# Jalankan dari akun Windows pemilik mesin Podman:
#   .\deploy\windows\perbaiki-mtu.ps1

[CmdletBinding()]
param([int]$Mtu = 1280)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "podman-common.ps1")

$unit = @"
[Unit]
Description=Lower eth0 MTU to $Mtu so TLS (SMTP, LDAPS, image pulls) does not stall on this network
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for i in `$(seq 1 30); do ip link show eth0 >/dev/null 2>&1 && break; sleep 1; done; ip link set dev eth0 mtu $Mtu'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"@

Wait-PodmanReady
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($unit.Replace("`r", "")))
$script = "echo $encoded | base64 -d | sudo tee /etc/systemd/system/hc-mtu.service >/dev/null && " +
          "sudo systemctl daemon-reload && sudo systemctl enable --now hc-mtu.service && ip link show eth0"
$result = Invoke-Podman -ArgumentList @("machine", "ssh", $script) -What "Memasang perbaikan MTU"
$line = $result.Output | Where-Object { $_ -match "mtu" } | Select-Object -First 1
Write-Host "Selesai. $line"
