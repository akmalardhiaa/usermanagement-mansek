# HC User Management

Portal Human Capital untuk akun karyawan di Active Directory: onboarding,
mutasi, resign/termination, dan perubahan profil. HC mengajukan di portal,
manager lalu tim CISO menyetujui **lewat email**, dan perubahan dijalankan ke
OU divisinya di AD.

```
 Staf HC (browser)              Manajer & tim CISO (email)
        │                                │ klik Setujui / Tolak
        ▼                                ▼
   portal  ── Python (FastAPI + Jinja2), port 3000 ──► SMTP kantor
        │   halaman HTML biasa, pengajuan, email, 2FA, data JSON
        │   (Bearer AD_SERVICE_TOKEN, jaringan internal)
        ▼
   ad-service  ── Java 21 (Spring Boot + UnboundID LDAP SDK), port 8081
        │   satu-satunya yang bicara ke AD; menolak menulis di luar OU kelola
        ▼
   Active Directory (LDAPS 636, sertifikat diverifikasi)
```

| Folder | Isi |
|---|---|
| [portal/](portal/) | Portal Python: `app/web` (halaman), `app/domain` (alur pengajuan, worker, email, sinkron AD), `app/security` (sesi, 2FA, pembatasan login), `templates`, `tests` |
| [ad-service/](ad-service/) | Layanan AD Java: login (`/v1/auth/login`), baca & tulis akun (`/v1/accounts…`), group, pemindahan OU |
| [deploy/windows/](deploy/windows/) | Skrip PC kantor: startup otomatis, backup, update, IIS `web.config`, template pengaturan production |
| [PANDUAN-PC-KANTOR.md](PANDUAN-PC-KANTOR.md) | Langkah memasang di PC kantor, dari nol sampai berjalan |

## Menjalankan di laptop

Butuh Podman (`winget install -e --id RedHat.Podman`). Klik dua kali
**`jalankan-podman.bat`**. Pertama kali ia membuat `.env.dev.local` dengan
rahasia acak, lalu membangun kedua container (beberapa menit). Buka
<http://localhost:3000>.

Atau dari terminal:

```powershell
Copy-Item .env.example .env.dev.local   # isi AD_SERVICE_TOKEN dan OUTBOX_ENCRYPTION_KEY
podman compose up -d --build
```

Menghentikan: `hentikan-podman.bat` atau `podman compose down`. Data demo ada di
volume `hc-dev-data`.

Tanpa `AD_LDAP_URL` dan dengan `DEMO_LOGIN=on`, layanan AD memakai akun demo:

| Username | Kata sandi | Peran portal |
| --- | --- | --- |
| `admin` | `admin12345` | Admin sistem + Human Capital — **satu-satunya yang bisa masuk** |
| `dimas`, `sarah`, `bagus`, `rina` | `<nama>12345` | tidak ada — login **ditolak** |

**Portal hanya untuk HC.** Manager dan tim CISO tidak punya akses portal: mereka
menyetujui dari email, lewat tautan sekali pakai yang terikat ke alamat mereka
sendiri. Akun demo lain sengaja ditolak, persis seperti manager di production.
Di mode demo email tidak terkirim; isinya ditulis sebagai berkas HTML di
`/app/data/outbox-mail` di dalam container portal. Akun demo selalu ditolak di
production.

## Alur pengajuan

1. **HC mengajukan** salah satu dari empat jenis. Formulir diperiksa di server
   (tanggal masuk akal, email sah, User ID sesuai aturan). Satu karyawan hanya
   boleh punya satu pengajuan berjalan.
2. **Manager** menerima email dengan tombol **Setujui** dan **Tolak**. Setujui
   langsung tercatat dalam satu klik; Tolak meminta satu konfirmasi.
3. **Tim CISO** — semua anggota `CISO_APPROVER_GROUP` (atau
   `CISO_APPROVER_EMAILS`) menerima email; **jawaban pertama yang berlaku**,
   tautan anggota lain lalu menyebut siapa yang sudah memutuskan.
4. **Worker** menjalankan perubahan ke AD lewat layanan AD, langkah demi
   langkah, lalu membaca ulang hasilnya sebelum menandai selesai.

| Jenis | Yang dilakukan di AD |
|---|---|
| Onboarding | Akun dibuat **nonaktif** di OU divisinya (`AD_DEPARTMENT_OU_PARENT` / `AD_DEPARTMENT_OUS`), atribut & manager diisi, group sesuai profil akses, dibaca ulang, baru diaktifkan |
| Mutasi | Divisi, jabatan, manager diperbarui; objek dipindah ke OU divisi baru; group diganti bila profil akses berubah |
| Resign / termination | **Pertama** dinonaktifkan, group dalam lingkup kebijakan dicabut, dipindah ke OU karantina — pagi hari setelah tanggal terakhir bekerja |
| Perubahan profil | Nama tampilan, divisi, jabatan |

Yang menjaga alurnya:
- Isi pengajuan disidik-jari saat dikirim; worker menolak berjalan bila isinya
  berubah, atau bila data karyawan berubah sejak disetujui.
- Langkah yang sudah selesai tidak diulang saat **Coba lagi**. Worker yang mati
  di tengah jalan meninggalkan catatan untuk dicocokkan dengan direktori dulu.
- Layanan AD hanya menulis bila `AD_LDAP_WRITE_ENABLED=true`, hanya di dalam
  `AD_MANAGED_OUS`, dan hanya group dari katalog `AD_ACCESS_GROUP_*`.
- Portal tidak pernah menulis password.
- Direktori karyawan diisi dari AD tiap `AD_SYNC_MINUTES` (hanya membaca, tidak
  pernah menghapus).
- Email antre dalam outbox terenkripsi, terkirim saat itu juga, diulang dengan
  jeda 1/5/15/30/60 menit bila gagal. Pengaturan email yang salah membuat
  antrean menunggu, bukan membuang email.

## Keamanan login dan halaman

- Login dengan akun AD (LDAPS saja), hanya untuk anggota group portal
  (`AD_GROUP_HC`, `AD_GROUP_ADMIN`, `AD_GROUP_OPS`, `AD_GROUP_AUDITOR`).
- Verifikasi 2 langkah dengan aplikasi authenticator (`LOGIN_2FA=on`), kunci
  2FA terenkripsi dengan `OUTBOX_ENCRYPTION_KEY`. Reset:
  `podman exec hc-portal python scripts/mfa_reset.py <username>`.
- 5 kali salah dalam 15 menit mengunci akun itu di portal selama 15 menit.
- Sesi dicocokkan ulang dengan AD tiap `SESSION_RECHECK_MINUTES`: akun yang
  dinonaktifkan atau keluar dari group otomatis keluar.
- Halaman tanpa JavaScript (`Content-Security-Policy: script-src 'none'`),
  kecuali satu skrip kecil di halaman persetujuan yang menekan **Setujui**
  untuk tautan dari email. Pemindai tautan email tidak menjalankan skrip,
  jadi tidak bisa menyetujui apa pun; GET biasa tidak pernah memutuskan.
- Setiap POST wajib berasal dari portal sendiri (Origin/Referer diperiksa).
- Catatan login ada di halaman **Status sistem**.

| Peran | Bisa |
|---|---|
| Human Capital | Direktori, export Excel, aktivitas, membuat & membatalkan pengajuan |
| Admin sistem / Operator | Direktori, aktivitas, pengajuan (lihat), **Status sistem**, **Coba lagi** |
| Auditor | Direktori, export, aktivitas, pengajuan (lihat) |

## Pengaturan

Semua pengaturan lewat variabel lingkungan; kedua container membaca berkas yang
sama.

- Laptop: [.env.example](.env.example) → `.env.dev.local`
- Kantor: [deploy/windows/env.production.example](deploy/windows/env.production.example) → `.env.production.local`

Halaman **Status sistem** (`/status`, admin/operator) memeriksa semuanya tanpa
mengubah apa pun: koneksi LDAPS, akun layanan, OU kelola, OU karantina, OU per
divisi, group CISO, mode tulis, sinkron, SMTP (sungguh tersambung dan login),
antrean email, 2FA, HTTPS.

## Pengembangan dan tes

```powershell
# Portal (Python 3.12)
cd portal
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q

# Layanan AD (Java 21, Maven)
cd ad-service
mvn test
```

Tes portal menjalankan seluruh alur lewat HTTP terhadap direktori tiruan di
memori: pengajuan → email → persetujuan satu klik → tim CISO jawaban pertama →
worker → akun di OU divisinya, plus penolakan, revisi, pembatalan, coba lagi,
sinkron, cek ulang sesi, dan antrean email. Tes layanan AD memakai server LDAP
di memori (UnboundID). CI ([.github/workflows/ci.yml](.github/workflows/ci.yml))
menjalankan keduanya dan membangun kedua image dengan Podman.

## Pindah ke PC kantor

`siapkan-pindah.bat` membangun kedua image dan menyimpannya sebagai satu berkas
`hc-images.tar.gz`, bersama berkas proyek tanpa rahasia dan data. Langkah di
kantor: [PANDUAN-PC-KANTOR.md](PANDUAN-PC-KANTOR.md).
