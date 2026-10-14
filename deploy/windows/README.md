# Deployment kantor

Target: **Windows Server 2022 + Hyper-V + Podman 6+**, tanpa WSL dan tanpa Compose.
Kompatibilitas Podman pada Server 2022 wajib diuji IT; belum tervalidasi di VM kantor.
Machine/koneksi khusus `hc-office`; container dijalankan lewat Podman CLI.

- [Permintaan untuk tim server](../../REQUEST-TIM-SERVER.md)
- [Panduan instalasi dan operasional](../../PANDUAN-PC-KANTOR.md)
- [Panduan AD, OU dan password awal](../../PANDUAN-AD-OU.md)

`office-runtime.ps1` mengelola dua container, network, volume data dan volume CA.
`start-portal.ps1` untuk startup; `kantor-jalankan.ps1` menerapkan perubahan .env.
`package-office.ps1` dijalankan di laptop; hasilnya arsip image + paket allowlist.
`backup-data.ps1` dan `update-portal.ps1` menggunakan koneksi kantor yang sama.
`register-startup-task.ps1` membutuhkan akun Windows pemilik machine.

`compose.production.yml` tetap alternatif deployment Compose, bukan jalur kantor ini.
`perbaiki-mtu.ps1` dan `kantor-common.ps1` adalah helper lama; tidak dipanggil
oleh jalur Hyper-V baru dan tidak disertakan dalam paket kantor.
