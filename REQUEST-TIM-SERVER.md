# Permintaan deployment HC User Management

Dokumen ini untuk tim server, jaringan, dan administrator Active Directory. Target deployment adalah **Windows Server native** memakai `HCUserManagement-Setup.exe`, dua Windows Service, dan IIS. Nilai kapasitas di bawah adalah usulan awal; tim infrastruktur perlu menyesuaikannya dengan beban dan kebijakan kantor.

## Permintaan untuk tim server

1. Siapkan VM Windows Server 2022 atau 2025 x64 sebagai **member server**, terpisah dari domain controller. Usulan awal: 4 vCPU, RAM 8 GiB, dan disk 80 GiB, ditambah kapasitas data serta backup. Berikan akses administrator untuk pemasangan dan uji UAT.
2. Pasang IIS Web Server, URL Rewrite, dan Application Request Routing (ARR) sebelum menjalankan installer. Sediakan DNS portal dan sertifikat HTTPS tepercaya dengan private key pada `LocalMachine\My`.
3. Izinkan HTTPS TCP 443 dari klien yang disetujui. Portal dan layanan AD hanya mendengar di `127.0.0.1:3000` dan `127.0.0.1:8081`; jangan buka kedua port itu ke jaringan.
4. Izinkan koneksi dari VM aplikasi ke domain controller melalui LDAPS TCP 636, DNS, dan layanan waktu kantor. Izinkan SMTP atau Microsoft Graph sesuai metode email yang dipilih.
5. Siapkan lokasi backup terenkripsi di luar disk aplikasi, retensi, pemantauan service, dan jadwal uji restore serta reboot. Data, konfigurasi, sertifikat, log, dan backup aplikasi berada di `C:\ProgramData\HCUserManagement`.
6. Jalankan uji instalasi bersih, upgrade, restart, uninstall, backup/restore, HTTPS, email, login, 2FA, dan operasi AD di VM UAT sebelum produksi. Installer belum ditandatangani dengan sertifikat perusahaan dan belum diuji pada server perusahaan.

## Permintaan untuk administrator AD

- Berikan FQDN domain controller, domain/UPN suffix, base DN, dan berkas CA PEM untuk memverifikasi sertifikat LDAPS.
- Siapkan service account dengan delegasi minimum hanya pada OU dan operasi yang disetujui. Kirim password melalui vault atau jalur rahasia kantor.
- Konfirmasi DN OU Kantor Pusat, Kantor Cabang, External, Intern, dan Karantina, termasuk sub-OU menurut [panduan AD/OU](PANDUAN-AD-OU.md).
- Konfirmasi grup role portal, grup akses yang diperlukan, alamat email manager dan CISO, serta proses pemberian password awal akun baru.
- Sediakan OU dan akun uji terisolasi. Mulai UAT dengan `AD_LDAP_WRITE_ENABLED=false`; aktifkan operasi tulis hanya setelah alur persetujuan dan delegasi diverifikasi.

## Jawaban yang perlu dikembalikan

- Versi/build Windows Server, kapasitas VM, DNS/IP, URL HTTPS, dan sertifikat yang akan dipakai.
- Kesiapan IIS, URL Rewrite, ARR, firewall, akses LDAPS, dan pilihan email SMTP/Graph.
- FQDN DC, base DN, UPN suffix, DN service account, OU, dan grup. Rahasia dikirim terpisah melalui vault.
- Lokasi dan retensi backup, pemilik operasional, serta jadwal dan kriteria lulus UAT.

Langkah instalasi dan operasi lengkap ada di [panduan Windows native](deploy/windows-native/README.md).
