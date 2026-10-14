# Struktur OU Active Directory untuk HC Portal

Panduan ini untuk administrator AD dan Windows Server. Portal dipasang pada member server Windows Server, terpisah dari domain controller. Nama dan DN di bawah adalah rancangan; salin `distinguishedName` sebenarnya dari AD sebelum mengisi konfigurasi. Nama `Intren`, `Invesment Banking`, `Settelment and Custody`, `Tecnology`, dan `Makasar` sengaja mengikuti daftar yang diberikan. Jika nama OU di AD berbeda, isi `AD_OU_NAME_MAP` setelah dikonfirmasi administrator AD.

## 1. Pohon OU yang disepakati

```text
DC=corp,DC=example,DC=com
├─ OU=Kantor Pusat
│  ├─ OU=Capital Market
│  ├─ OU=CEO Functional
│  ├─ OU=Generic
│  ├─ OU=Intren
│  ├─ OU=Invesment Banking
│  ├─ OU=Operations
│  ├─ OU=Retail
│  ├─ OU=Settelment and Custody
│  └─ OU=Tecnology
├─ OU=Kantor Cabang
│  ├─ OU=Bandung
│  ├─ OU=Batam
│  ├─ OU=Jakarta
│  ├─ OU=Makasar
│  ├─ OU=Medan
│  ├─ OU=Semarang
│  ├─ OU=Singapore Subsidiary
│  └─ OU=Surabaya
├─ OU=External
│  ├─ OU=Pusat
│  └─ OU=Cabang
│     ├─ OU=Bandung
│     ├─ OU=Batam
│     ├─ OU=Jakarta
│     ├─ OU=Makasar
│     ├─ OU=Medan
│     ├─ OU=Semarang
│     ├─ OU=Singapore Subsidiary
│     └─ OU=Surabaya
├─ OU=Intern
└─ OU=Karantina
```

`OU=Karantina` adalah usulan untuk akun resign atau nonaktif permanen. Administrator AD harus menyetujui pohon OU sebelum skrip pembuatan dijalankan. Installer tidak membuat OU otomatis. Di ADUC, aktifkan **View > Advanced Features**, buka **Properties > Attribute Editor**, lalu salin `distinguishedName`. RSAT PowerShell dapat membaca daftar dengan:

```powershell
Get-ADOrganizationalUnit -Filter * | Select-Object Name,DistinguishedName
```

## 2. Aturan penempatan akun

| Jenis pekerja | Lokasi | OU tujuan |
|---|---|---|
| Internal | Pusat | Divisi yang dipilih di bawah `Kantor Pusat` |
| Internal | Cabang | Kota yang dipilih di bawah `Kantor Cabang` |
| External | Pusat | `External > Pusat` |
| External | Cabang | Kota yang dipilih di bawah `External > Cabang` |
| Intern | Pusat | `Intern` langsung di bawah domain |

Jenis pekerja **Internal/External/Intern** terpisah dari status kepegawaian **Tetap/Kontrak**. Akun baru otomatis mendapat grup dasar `AD_ACCESS_GROUP_BASE`; formulir tidak meminta profil akses. Mutasi tidak mengubah grup akses. Pada onboarding, akun dibuat dalam keadaan disabled di OU tujuan, atribut dan grup diisi, lalu akun diaktifkan setelah langkah wajib selesai. Pada mutasi atau perubahan profil yang mengubah penempatan, akun dipindah ke OU tujuan setelah persetujuan. Pada termination, akun dinonaktifkan terlebih dahulu, grup akses katalog dicabut, lalu akun dipindah ke `Karantina`. Disable manual di AD tidak otomatis memindahkan akun; status enabled di AD tetap sumber kebenaran.

Untuk akun yang sudah ada, sinkronisasi membaca OU guna mengisi jenis pekerja dan lokasi bila OU cocok dengan pohon di atas. Akun di OU lain tidak ditebak kategorinya. Periksa dan pilih penempatan dengan benar saat membuat pengajuan.

## 3. Konfigurasi Windows Server

Sesudah memasang installer native, isi `C:\ProgramData\HCUserManagement\config\production.env` dari template yang dipasang. Berikut contoh **DN fiktif**; ganti setiap nilai dengan DN sebenarnya:

```dotenv
AD_BASE_DN=DC=corp,DC=example,DC=com
AD_LDAP_URL=ldaps://dc01.corp.example.com:636
AD_OU_ROUTING_MODE=STRUCTURED
AD_OU_INTERNAL_PUSAT_PARENT=OU=Kantor Pusat,DC=corp,DC=example,DC=com
AD_OU_INTERNAL_CABANG_PARENT=OU=Kantor Cabang,DC=corp,DC=example,DC=com
AD_OU_EXTERNAL_PUSAT=OU=Pusat,OU=External,DC=corp,DC=example,DC=com
AD_OU_EXTERNAL_CABANG_PARENT=OU=Cabang,OU=External,DC=corp,DC=example,DC=com
AD_OU_INTERN=OU=Intern,DC=corp,DC=example,DC=com
AD_COMPANY_NAME=PT Mandiri Sekuritas
AD_OU_NAME_MAP=
AD_QUARANTINE_OU=OU=Karantina,DC=corp,DC=example,DC=com
AD_MANAGED_OUS=OU=Kantor Pusat,DC=corp,DC=example,DC=com;OU=Kantor Cabang,DC=corp,DC=example,DC=com;OU=External,DC=corp,DC=example,DC=com;OU=Intern,DC=corp,DC=example,DC=com;OU=Karantina,DC=corp,DC=example,DC=com
AD_SYNC_OUS=OU=Kantor Pusat,DC=corp,DC=example,DC=com;OU=Kantor Cabang,DC=corp,DC=example,DC=com;OU=External,DC=corp,DC=example,DC=com;OU=Intern,DC=corp,DC=example,DC=com;OU=Karantina,DC=corp,DC=example,DC=com
AD_LDAP_WRITE_ENABLED=false
```

`AD_MANAGED_OUS` membatasi lokasi tulis pada layanan AD. `AD_SYNC_OUS` menentukan lokasi baca untuk direktori karyawan; sertakan `Karantina` agar status akun yang dipindah tetap terlacak. Validasi konfigurasi menolak OU tujuan di luar kedua lingkup tersebut. Isi juga DN akun layanan, grup portal, grup akses, sertifikat CA LDAPS, HTTPS, email, dan rahasia lain sesuai `PANDUAN-INSTALASI.md` pada folder aplikasi. Simpan sertifikat CA publik sebagai PEM di folder `C:\ProgramData\HCUserManagement\certificates`; rujuk file itu lewat `LDAP_CA_CERT_PATH`.

Jika nama OU nyata tidak sama dengan label formulir, isi pasangan `label=>nama OU sebenarnya` dipisahkan `;`, misalnya `AD_OU_NAME_MAP=Makasar=>Makassar;Intren=>Intern`. Hanya nama **leaf OU** yang diubah; DN induk tetap harus diisi persis. Jangan menebak ejaan: cek DN nyata di AD terlebih dahulu. Halaman Status sistem memeriksa semua OU tujuan hasil pemetaan sebelum mode tulis diaktifkan.

## 4. Hak akses dan uji

Akun layanan AD harus bisa membaca objek user, manager, OU, dan grup persetujuan; membuat serta mengubah user pada OU tujuan; memindahkan user antar OU yang dikelola; dan mengubah membership hanya pada grup akses katalog. Delegasikan hak minimum pada OU dan grup yang tepat. Jangan memakai Domain Admin sebagai akun layanan. Aplikasi juga membatasi penulisan lewat `AD_MANAGED_OUS`, tetapi ACL AD tetap pengaman utama.

Mulai UAT dengan `AD_LDAP_WRITE_ENABLED=false`. Jalankan elevated:

```powershell
& 'C:\Program Files\HCUserManagement\validate-config.ps1'
& 'C:\Program Files\HCUserManagement\health-check.ps1' -CheckAd
```

Buka halaman **Status sistem** untuk memeriksa seluruh 27 OU penempatan, OU Karantina, koneksi AD, dan grup CISO. Setelah administrator AD menyetujui akun/OU uji, aktifkan mode tulis pada konfigurasi UAT dan restart kedua service. Uji onboarding Internal Pusat, Internal Cabang, External Pusat, External Cabang, dan Intern; mutasi antar lokasi dan jenis; perubahan profil; serta termination. Untuk tiap uji, periksa `Enabled`, `DistinguishedName`, atribut, dan grup melalui ADUC atau:

```powershell
Get-ADUser -Identity hc.uji -Properties Enabled,DistinguishedName,Department,Title,Manager,MemberOf,PasswordLastSet |
    Select-Object SamAccountName,Enabled,DistinguishedName,Department,Title,Manager,MemberOf,PasswordLastSet
```

Layanan saat ini tidak menetapkan password awal AD. Jika kebijakan domain menolak enable akun tanpa password, akun tetap disabled; operator AD yang berwenang menetapkan password sesuai kebijakan lalu admin portal menggunakan **Coba lagi** pada pengajuan yang sama. Jangan membuat pengajuan duplikat atau melonggarkan kebijakan password.

## 5. OU Intern dan atribut akun

`OU=Intern` berdiri sendiri di bawah domain, terpisah dari `OU=Intren` lama di bawah Kantor Pusat. Pilih jenis pekerja **Intern** dan lokasi **Kantor Pusat** dalam dashboard. Aplikasi baru menulis akun ke OU setelah persetujuan Manager dan CISO selesai. Jumlah OU tujuan yang diperiksa halaman Status sistem sekarang 27.

Skrip berikut disertakan dalam installer. Jalankan dari server dengan RSAT ActiveDirectory memakai akun administrator AD yang didelegasikan. Panggilan pertama hanya pratinjau; `-Apply` membuat OU yang belum ada, termasuk Intern, tanpa memindahkan akun yang sudah ada:

```powershell
& 'C:\Program Files\HCUserManagement\create-ad-ous.ps1' -BaseDn 'DC=corp,DC=example,DC=com'
& 'C:\Program Files\HCUserManagement\create-ad-ous.ps1' -BaseDn 'DC=corp,DC=example,DC=com' -Apply
```

`sAMAccountName` berasal dari User ID, misalnya `AMUMUBAROK367`. `userPrincipalName` memakai User ID huruf kecil dan `LDAP_DOMAIN`, misalnya `amumubarok367@mandirisekuritas.co.id` bila `LDAP_DOMAIN=mandirisekuritas.co.id`. `cn` dan `displayName` memakai Nama lengkap; `givenName` Nama depan; `sn` Nama belakang; `mail` Email kantor; `manager` diambil dari akun manager aktif; `department` dari divisi; `title` dari jabatan; `description` dari Keterangan jabatan (misalnya `TI Helpdesk`); `physicalDeliveryOfficeName` dari lokasi (misalnya `Kantor Pusat`); `company` dari `AD_COMPANY_NAME`. `distinguishedName` dibentuk oleh AD dari CN dan OU, sehingga tidak diisi bebas. `accountExpires` memakai tanggal Akhir kontrak dan kedaluwarsa pukul 00.00 WIB pada hari sesudahnya; status Tetap memakai 0, artinya tidak kedaluwarsa.

Jangan mengaktifkan tulis produksi sebelum nama OU, DN, ACL, sertifikat, backup, serta alur persetujuan dan UAT di Windows Server selesai diverifikasi.
