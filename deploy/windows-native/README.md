# Deployment native Windows Server

Installer `HCUserManagement-Setup.exe` memuat portal FastAPI, Java AD Service, Python embeddable, Java 21 runtime, dan WinSW. Kode dipasang di `C:\Program Files\HCUserManagement`; data, konfigurasi, sertifikat, log, dan backup ada di `C:\ProgramData\HCUserManagement`. Dua service berjalan sebagai LocalService dan hanya mendengar di `127.0.0.1:3000` dan `127.0.0.1:8081`. IIS menerima HTTPS 443 dan meneruskan ke portal. Gunakan satu instance portal dan data pada disk lokal, karena penyimpanan JSON memakai lock per proses.

**Status keamanan:** installer belum ditandatangani dan belum diuji pada Windows Server, IIS, sertifikat, atau AD perusahaan. Lulus build dan health lokal tidak berarti siap produksi. Tim IT harus menguji instalasi, reboot, backup/restore, HTTPS, dan alur AD pada VM UAT sebelum digunakan.

## Prasyarat server

- Windows Server 2022 atau 2025 x64 **member server**, bukan domain controller; jalankan setup sebagai Administrator. Sediakan ruang kosong minimal 2 GB pada drive instalasi, ditambah kapasitas data dan backup.
- Pasang IIS Web-Server, IIS URL Rewrite, dan Application Request Routing (ARR) **sebelum setup**. Setup memeriksa komponen tersebut dan menolak instalasi bila belum tersedia. Siapkan DNS portal dan sertifikat HTTPS tepercaya dengan private key di `LocalMachine\My`.
- Siapkan FQDN DC, LDAPS port 636, sertifikat CA PEM, domain, base DN, OU, grup akses, dan service account AD dengan delegasi minimum. Siapkan SMTP atau Graph untuk email.
- Blokir akses jaringan eksternal ke port 3000 dan 8081. Izinkan hanya HTTPS 443 dari klien sesuai kebijakan firewall.
- Gunakan disk backup terenkripsi yang terpisah dari disk aplikasi. Data backup memuat secret. Simpan kunci `OUTBOX_ENCRYPTION_KEY` juga di vault yang terpisah.

## Build di mesin build

Siapkan uv, Maven, JDK 21 atau lebih baru, Inno Setup 6, Python embeddable 3.13.16 x64 ZIP, Temurin JRE 21 x64 ZIP, dan WinSW x64. Verifikasi SHA-256 dan lisensi masing-masing sumber. Dari root repo:

```powershell
.\deploy\windows-native\build-package.ps1 -PythonEmbedZip C:\media\python-3.13.16-embed-amd64.zip -JavaRuntimeZip C:\media\temurin-jre21.zip -WinSWExe C:\media\WinSW.exe -Uv C:\tools\uv.exe -Maven C:\tools\maven\bin\mvn.cmd
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' .\deploy\windows-native\installer.iss
Get-FileHash .\artifacts\windows-native\HCUserManagement-Setup.exe -Algorithm SHA256
```

Setup tidak memuat `production.env`, private key, atau sertifikat perusahaan. Verifikasi SHA-256 setelah transfer. Tanda tangani EXE dengan sertifikat code signing perusahaan sebelum distribusi produksi. Penggunaan komersial Inno Setup memerlukan perhatian pada [ketentuan lisensi resminya](https://jrsoftware.org/isorder.php).

## Instalasi awal

1. Jalankan EXE sebagai Administrator dan simpan log, misalnya `/LOG="C:\temp\hc-setup.log"`. Pemeriksaan awal mengecek OS, IIS/ARR/URL Rewrite, ruang disk, konflik service, port lokal, serta konfigurasi jika sudah ada. Kegagalan menghentikan setup dengan kode bukan nol.
2. Tanpa `production.env`, setup memasang file dan mengatur ACL, lalu menunggu konfigurasi. Salin `C:\ProgramData\HCUserManagement\config\env.production.native.example` ke `production.env`. Isi semua placeholder dan nilai wajib. Gunakan `APP_ENV=production`, `DEMO_LOGIN=off`, `LOGIN_2FA=on`, `AD_SERVICE_URL=http://127.0.0.1:8081`, `AD_OU_ROUTING_MODE=STRUCTURED`, dan `AD_LDAP_WRITE_ENABLED=false` untuk UAT awal. Siapkan Kantor Pusat, Kantor Cabang, External, Intern, dan Karantina sesuai [pohon OU](../../PANDUAN-AD-OU.md). Skrip `create-ad-ous.ps1` dalam folder aplikasi menyediakan pratinjau dan pembuatan OU dengan `-Apply` untuk administrator AD. Isi `AD_OU_INTERN` dan `AD_COMPANY_NAME`; validasi konfigurasi mengharuskan seluruh OU tujuan berada dalam `AD_MANAGED_OUS` dan `AD_SYNC_OUS`.
3. Tempatkan CA LDAPS PEM di folder `certificates`. Pastikan FQDN DC cocok dengan sertifikat. Jalankan elevated `& 'C:\Program Files\HCUserManagement\validate-config.ps1'`.
4. Jalankan elevated `& 'C:\Program Files\HCUserManagement\finalize-install.ps1' -InstallDir 'C:\Program Files\HCUserManagement'`. Skrip mengatur ACL, mendaftarkan dan menjalankan kedua service, lalu menguji health HTTP lokal. Jika konfigurasi sudah ada saat setup, finalisasi dilakukan otomatis dan kegagalannya menghasilkan kode setup bukan nol.
5. Jalankan elevated `& 'C:\Program Files\HCUserManagement\install-iis.ps1' -DnsName portal.example.internal -CertificateThumbprint <thumbprint>`. Nama DNS harus cocok dengan `APP_BASE_URL` dan SAN sertifikat. Uji HTTPS dari klien, cookie aman, serta alur login/approval. Health lokal hanya membuktikan service HTTP aktif; uji AD dan email tetap diperlukan.

`production.env` memakai format `NAMA=nilai`, satu per baris, tanpa quote atau ekspansi `$env:`. Kedua service membacanya saat start. Restart kedua service setelah mengubah nilai. Jangan ganti `OUTBOX_ENCRYPTION_KEY` tanpa migrasi data terenkripsi.

## Operasi

- Cek service dan health: `Get-Service HC-*`; `& 'C:\Program Files\HCUserManagement\health-check.ps1' -CheckAd`. Opsi `-CheckAd` hanya menguji TCP 636; login AD menguji bind dan validasi sertifikat sebenarnya.
- Uji baca AD dan role HC/Admin/Ops/Auditor, 2FA, SMTP/Graph, dan status sistem. Aktifkan `AD_LDAP_WRITE_ENABLED=true` hanya setelah izin perubahan AD. Uji tulis pada OU/akun UAT terisolasi dan jalur approval Manager+CISO.
- Backup: elevated `& 'C:\Program Files\HCUserManagement\backup-data.ps1' -Destination 'D:\safe\hc-backup-20261014'`. Portal berhenti sementara saat menyalin folder `data`, `config`, dan `certificates`; `manifest.json` memuat SHA-256 tiap file. Lindungi dan salin backup ke lokasi terpisah dari server.
- Restore: elevated `& 'C:\Program Files\HCUserManagement\restore-data.ps1' -BackupDirectory 'D:\safe\hc-backup-20261014'`. Skrip memverifikasi manifest, membuat backup penyelamat, menyalin data, dan memverifikasi hasil. Latih restore di VM UAT sebelum dibutuhkan di produksi.
- Update: simpan installer versi lama untuk pemulihan kode. Jalankan elevated `& 'C:\Program Files\HCUserManagement\update-application.ps1' -InstallerPath 'D:\media\HCUserManagement-Setup.exe' -ExpectedSha256 '<64-digit-sha256>'`. Skrip memverifikasi hash, menjalankan setup dengan log, lalu memeriksa health. Setup membuat backup sebelum mengganti file. Jika penggantian file gagal, backup data tetap ada dan kode lama perlu dipasang ulang dari installer lama.
- Uninstall melalui Programs and Features melepas service dan kode. Folder ProgramData berisi data, konfigurasi, sertifikat, log, dan backup tetap tersimpan.

## Gerbang produksi

Installer yang dibangun pada mesin ini belum diuji pada Windows Server perusahaan. Jalankan uji VM UAT untuk instalasi bersih, upgrade, restart dan reboot, uninstall, restore, IIS/TLS, firewall, log tanpa kredensial, seluruh role, dan alur AD. Verifikasi lisensi runtime, signature dan SHA-256 EXE, serta persetujuan IT/Security. Tidak ada skrip instalasi yang dapat menjamin keberhasilan pada konfigurasi server yang belum diperiksa.
