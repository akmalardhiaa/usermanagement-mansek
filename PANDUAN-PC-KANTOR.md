# Menjalankan HC Portal di VM Windows Server 2022 kantor

Jalur yang disiapkan: Windows Server 2022 x64 -> Hyper-V -> Podman 6+ ->
dua container Linux. Tidak menggunakan WSL, Docker Desktop/Engine, atau Compose.
Konfirmasikan edisi/build Windows dan versi Podman yang disetujui IT.
Microsoft mendukung role Hyper-V pada Server 2022. Namun, panduan Podman Windows
terbaru menyebut Windows 11 atau lebih baru dan tidak memastikan Server 2022.
Jalur ini adalah kandidat deployment, bukan klaim dukungan resmi Podman di Server 2022.
IT wajib menyetujui versi dan membuktikan init/start machine, akses jaringan,
forwarding localhost, serta startup tanpa login pada VM ini sebelum produksi.
Konfirmasikan instalasi Desktop Experience atau Server Core; pada Core gunakan
PowerShell dan administrasi jarak jauh, bukan mengandalkan klik desktop.
Jika kombinasi ini tidak lolos, hentikan deployment dan sepakati alternatif
VM Linux dengan Podman bersama IT; jangan mengaktifkan WSL atau Docker sebagai fallback.
VM aplikasi terpisah dari domain controller AD; tidak perlu memasang AD DS di VM aplikasi.

Baca [REQUEST-TIM-SERVER.md](REQUEST-TIM-SERVER.md) untuk permintaan infrastruktur
dan [PANDUAN-AD-OU.md](PANDUAN-AD-OU.md) untuk akun layanan, OU dan alur user.
Skrip disiapkan untuk lingkungan ini, tetapi penerimaan di VM kantor tetap wajib:
pemeriksaan lokal kode tidak membuktikan Hyper-V, jaringan kantor, atau AD bekerja.

## 1. Yang disiapkan tim server

- Nested virtualization diaktifkan di host untuk VM Windows yang diberikan.
  Hyper-V juga diaktifkan DI DALAM VM Windows, lalu restart.
- Sebagai ukuran awal (belum hasil load test): Windows VM 4 vCPU, RAM 8 GiB,
  disk 80 GiB dengan ruang untuk image, VM Linux, data dan backup.
  Skrip membuat VM Linux Podman dengan 2 vCPU, RAM 4 GiB, disk 40 GiB.
- Akun operator Windows tetap, anggota Hyper-V Administrators, boleh menjalankan
  Task Scheduler saat boot meskipun belum login. Akun ini digunakan konsisten.
  Jangan membuat machine sebagai akun administrator lain lalu menjalankannya
  sebagai operator: koneksi, kunci SSH dan machine terkait profil pengguna.
- MSI Podman 6+ x64 resmi, pilih provider Hyper-V. Tidak perlu Podman Desktop.
  IT dapat menjalankan `podman system hyperv-prep` sesuai dokumentasi versi
  terpasang untuk akun operator; jangan menambahkan akun yang salah ketika elevasi.
- IIS, URL Rewrite, ARR, DNS portal dan sertifikat HTTPS dari IT.
- Akses DNS kantor, DC LDAPS 636, relay email atau Graph, serta waktu yang sinkron.

`deploy\windows\kantor-prasyarat.ps1` hanya MEMERIKSA prasyarat dan menampilkan
langkah instalasi IT. Jalankan sebagai Administrator untuk membaca status fitur.
Berkas ini tidak lagi memasang software atau menyalakan WSL.
IT dapat mengaktifkan Hyper-V dengan PowerShell Administrator:

```powershell
Install-WindowsFeature -Name Hyper-V -IncludeManagementTools
# Setelah restart yang dijadwalkan IT:
Get-WindowsFeature Hyper-V, Hyper-V-PowerShell
```

Restart mengikuti arahan IT. Nested virtualization harus lebih dulu tersedia
di host. Bila host VMware/Azure, tim server mengikuti prosedur platform tersebut.

## 2. Buat paket di laptop

Nyalakan Podman laptop dan pastikan `podman info` berhasil. Laptop boleh tetap
memakai backend pengembangannya; paket tidak mensyaratkan WSL pada kantor.

Jalankan `powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\package-office.ps1 -Destination D:\kirim-ke-kantor`. Skrip:

1. Membangun dua image Linux amd64 dari kode terbaru.
2. Membuat folder paket baru (tidak menimpa/menghapus folder tujuan lama).
3. Menyalin hanya daftar berkas deployment dan panduan yang ditentukan.
4. Mengekspor `hc-images.tar`, SHA-256 dan ZIP.

Hasil: `<tujuan>\hc-package-<waktu>-<id>\hc-portal-kantor.zip`.
Arsip image multi-image memakai format yang kompatibel dengan Docker,
tetapi dibuat/dimuat oleh Podman; tidak memerlukan produk Docker.
Tidak menyertakan .env nyata, sertifikat, data demo, log atau repository git.
Build memerlukan akses registry image dasar serta dependency Python/Maven
di laptop. Jangan kirim folder proyek mentah yang berisi rahasia.

Paket aplikasi ini bukan paket instalasi Podman offline lengkap.
Inisialisasi Hyper-V pertama juga perlu image OS Podman dari registry.
Untuk kantor tanpa internet, IT harus menyiapkan installer dan image OS/cache
yang cocok dengan versi Podman melalui proses offline yang mereka dukung.
Jangan menganggap `hc-images.tar` sudah berisi OS Podman.

## 3. Siapkan aplikasi di kantor

1. Ekstrak paket; taruh folder `hc-portal` misalnya di `C:\hc-portal`.
2. Batasi ACL folder ke akun operator dan administrator. .env berisi rahasia.
3. Login sebagai operator yang akan menjalankan startup/backup.
4. Jalankan `powershell -NoProfile -File .\deploy\windows\kantor-siapkan.ps1`.
   Skrip memilih provider Hyper-V dan koneksi `hc-office`, membuat machine bila
   belum ada, memverifikasi SHA-256 dan memuat image.
5. Isi `.env.production.local` yang dibuat dari template. Kunci service dan
   enkripsi dibuat sekali secara acak; tidak diganti ketika setup diulang.
6. Simpan CA publik/root+intermediate yang diperlukan sebagai
   `certs\corp-root-ca.pem`. Jangan sertakan private key DC.
7. Isi URL HTTPS portal di `APP_BASE_URL`. Placeholder pengaturan yang tidak
   digunakan harus dikosongkan, bukan dibiarkan berupa `<...>`.
8. Simpan .env UTF-8; nilai tanpa kutip, tanpa `export`, tanpa komentar di ujung
   nilai. Karakter `$` pada password adalah literal, bukan interpolasi Compose.

Setting wajib AD/OU dijelaskan pada panduan AD. Biarkan
`AD_LDAP_WRITE_ENABLED=false` saat uji koneksi. Jalur kantor memakai dua volume:
`hc-portal-data` untuk data persisten dan `hc-portal-certs` untuk CA publik.
CA disalin memakai `podman cp` lalu dipasang read-only di kedua container,
jadi tidak bergantung pada kemampuan mount drive Windows di Hyper-V.
Bila SMTP memakai CA internal, isi `SMTP_CA_CERT_PATH=/run/certs/corp-root-ca.pem`
dan sertakan rantai CA SMTP dalam bundle itu.

## 4. IIS dan HTTPS

Tim server membuat situs IIS terpisah dari folder .env/proyek, memasang binding
HTTPS pada nama portal, dan menaruh `deploy\windows\web.config` di root situs.
Ganti `hc.example.internal` dengan nama portal sebenarnya.
Aktifkan proxy ARR dengan preserveHostHeader dan izinkan
`HTTP_X_FORWARDED_PROTO` sesuai komentar di web.config.

IIS meneruskan ke `http://127.0.0.1:3000`; container AD port 8081 tidak
dipublikasikan ke Windows/LAN. Jangan membuka 3000 dan 8081 ke pengguna.
Pengguna dan tautan approval email memakai HTTPS 443 pada IIS.
Bila IT memakai proxy di server lain, konfigurasi ini perlu disesuaikan karena
backend sekarang hanya terikat ke loopback Windows VM aplikasi.

## 5. Menjalankan dan memeriksa

Jalankan `powershell -NoProfile -File .\deploy\windows\kantor-jalankan.ps1`.
Skrip ini menerapkan .env/image/CA terbaru dengan
mengganti kedua container milik aplikasi. Akan ada jeda layanan singkat.
Volume data dipertahankan. Resource bernama sama tanpa label kepemilikan aplikasi
ditolak, bukan diambil alih. Pada boot, `start-portal.ps1` cukup menyalakan
container yang ada tanpa menggantinya.

Skrip menunggu HTTP portal dan Java menjawab. Ini belum membuktikan bind AD.
Buka URL HTTPS dari browser, login memakai akun AD yang diberi group portal,
daftarkan 2FA, lalu buka **Status sistem** sebagai admin/operator.
Periksa bind service, OU, group, SMTP/Graph, dan kondisi read-only.
Peringatan mode tulis mati memang diharapkan pada tahap ini.

Bila gagal, jalankan `powershell -NoProfile -File .\deploy\windows\office-status.ps1`; samarkan data pribadi sebelum membagikan
log. MTU tidak diubah otomatis. Bila ada masalah DNS/TLS, periksa dari container,
bukan hanya dari Windows, karena jalurnya melewati VM Linux dan network container:

```powershell
$env:CONTAINERS_MACHINE_PROVIDER = 'hyperv'
$env:CONTAINER_CONNECTION = 'hc-office'
podman machine list
podman info
podman exec hc-ad-service getent hosts dc01.corp.example.com
Test-NetConnection dc01.corp.example.com -Port 636
```

Ganti hostname contoh. `Test-NetConnection` hanya tes TCP dari Windows; halaman
Status sistem melakukan bind lewat layanan Java dan memverifikasi sertifikat.
Status `directory=ldaps` saja hanya menyatakan konfigurasi, bukan koneksi berhasil.

## 6. Startup, backup, update, pemulihan

Dari PowerShell Administrator, daftarkan startup dengan akun operator yang sama:

```powershell
powershell -NoProfile -File .\deploy\windows\register-startup-task.ps1
```

IT harus mengizinkan logon as batch job sesuai kebijakan akun. Uji reboot VM:
portal harus kembali tersedia tanpa sesi RDP/login interaktif.

Backup manual (jadwalkan harian lewat Task Scheduler dengan akun yang sama):

```powershell
powershell -NoProfile -File .\deploy\windows\backup-data.ps1 -BackupPath D:\HC-Backups
```

Backup menghentikan portal sebentar agar JSON konsisten, mengekspor volume,
kemudian menghidupkan lagi hanya jika sebelumnya berjalan.
Arsip lama tidak dihapus otomatis; retensi dan salinan di luar VM dikelola IT.
Simpan .env (terutama OUTBOX_ENCRYPTION_KEY), CA dan konfigurasi IIS secara terpisah
di penyimpanan yang diizinkan. Kehilangan kunci berarti data terenkripsi/2FA
tidak dapat dibuka hanya dari arsip volume.

Update: buat paket baru di laptop, simpan paket lama untuk rollback, salin
skrip/panduan baru tanpa menimpa .env. Jalankan dari folder aplikasi:

```powershell
powershell -NoProfile -File .\deploy\windows\update-portal.ps1 -ImageArchive D:\paket-baru\hc-images.tar
```

Checksum harus di sebelah arsip. Update mengambil backup sebelum memuat image
dan mengganti container. Tidak menjalankan git pull atau build dependency di kantor.

Pemulihan harus diuji di machine/VM terpisah sebelum produksi: setup paket yang
cocok, simpan kembali .env/kunci yang sama, jalankan pertama kali untuk membuat
volume, hentikan container, lalu impor backup ke volume data KOSONG yang sudah
diverifikasi. `podman volume import hc-portal-data <arsip>` mengekstrak isi arsip;
jangan menimpa volume aktif atau menganggap import membersihkan data lama.
Uji login/2FA, pengajuan dan audit setelah pemulihan. Rollback image menggunakan
arsip versi sebelumnya dengan prosedur update; restore data juga diperlukan bila
format datanya berubah.

## 7. Uji penerimaan sebelum dipakai HC

- HTTPS dari PC HC/manager berfungsi; login dan 2FA berhasil.
- Status sistem membuktikan bind akun service, OU dan email.
- Uji akun dummy di OU uji: approval manager lalu CISO, objek AD muncul di OU tepat.
- Terapkan prosedur password awal pada panduan AD, lalu verifikasi login akun.
- Uji mutasi antar-OU dan termination ke karantina.
- Reboot tanpa login operator; layanan kembali hidup.
- Backup dan restore di lingkungan terpisah terbukti.
- Baru gunakan OU/data produksi sesuai persetujuan pemilik AD.

Rujukan:
[instalasi Hyper-V Windows Server](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v),
[Podman Windows/Hyper-V](https://github.com/podman-container-tools/podman/blob/main/docs/tutorials/podman-for-windows.md),
[nested virtualization Microsoft](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/enable-nested-virtualization).
