# Deploy HC User Management di Podman Windows

> Pertama kali memasang? Mulai dari **[PANDUAN-PC-KANTOR.md](../../PANDUAN-PC-KANTOR.md)**
> — urutan langkah demi langkah dengan cek di setiap bagian. Dokumen ini
> rinciannya.

Production menjalankan dua container Linux di Podman machine (WSL2):

| Container | Image | Port | Tugas |
|---|---|---|---|
| `hc-portal` | `hc-portal:latest` (Python 3.12) | `127.0.0.1:3000` | Halaman HC, pengajuan, email, worker, sinkron, 2FA, data JSON di volume `hc-portal-data` |
| `hc-ad-service` | `hc-ad-service:latest` (Java 21) | tidak ada | Login AD dan semua baca/tulis direktori lewat LDAPS 636 |

IIS menangani HTTPS dan meneruskan request ke `127.0.0.1:3000`. Portal
memanggil layanan AD di jaringan internal compose (`http://ad-service:8081`)
dengan `Authorization: Bearer <AD_SERVICE_TOKEN>`; tanpa token yang sama,
layanan AD menolak setiap permintaan kecuali `/v1/health`.

## Prasyarat

- Windows Server 2022 / Windows 11 dengan WSL2 dan virtualisasi diaktifkan.
- Podman CLI untuk Windows dan provider `podman compose`
  (`winget install -e --id RedHat.Podman` dan
  `winget install -e --id Docker.DockerCompose` — gratis, bukan Docker Desktop).
- Podman machine dengan memori **4 GB** (`podman machine init --memory 4096`,
  atau `podman machine set --memory 4096` untuk yang sudah ada): JVM dan
  Python berjalan bersama.
- IIS URL Rewrite, Application Request Routing (ARR), dan sertifikat HTTPS
  untuk nama portal.
- Jaringan ke DNS kantor dan DC LDAPS, serta akun service AD dengan hak
  minimum yang disetujui tim AD.
- Satu akun Windows khusus/terkelola yang memiliki Podman machine. Jangan
  menjalankan machine sebagai SYSTEM; machine Podman bersifat per-user.

## Image

Dua cara, pilih salah satu:

- **Tanpa internet** (disarankan): di laptop, `siapkan-pindah.bat` membangun
  kedua image dan menyimpannya sebagai `hc-images.tar.gz`. Di server:
  `podman load -i hc-images.tar.gz`.
- **Dengan internet**: `podman compose -f compose.production.yml build`
  membangun dari folder `portal` dan `ad-service` (mengunduh image Python,
  Maven, dan Java; beberapa menit).

## CA ke PEM

1. Buka `certlm.msc`.
2. Temukan CA internal yang menerbitkan sertifikat LDAPS DC.
3. Klik kanan → **All Tasks** → **Export** → **Base-64 encoded X.509 (.CER)**;
   simpan sebagai `C:\hc-portal\certs\corp-root-ca.pem`.

Java di container tidak membaca Windows certificate store. Berkas itu di-mount
read-only ke layanan AD sebagai `/run/certs/corp-root-ca.pem`
(`LDAP_CA_CERT_PATH`). Layanan AD hanya menerima LDAPS port 636, TLS 1.2/1.3,
dan memeriksa bahwa nama host DC di `AD_LDAP_URL` cocok dengan sertifikatnya.

### Uji DNS dari container

```powershell
podman exec hc-ad-service getent hosts <nama-host-dc>
```

Kosong berarti container tidak bisa menemukan DC lewat DNS: periksa DNS
Podman machine (`podman machine ssh -- cat /etc/resolv.conf`) bersama IT.

## Jalankan dan daftarkan startup

`certs\corp-root-ca.pem`, `.env.production.local`, dan kedua image harus sudah
tersedia:

```powershell
podman compose -f compose.production.yml up -d
podman compose -f compose.production.yml ps
```

Volume `hc-portal-data` menyimpan JSON, sesi, 2FA, outbox, dan catatan login
pada filesystem Linux Podman machine. Jangan menggantinya dengan bind mount ke
disk Windows: store mengandalkan rename atomik.

Untuk mulai otomatis setelah Windows boot, buka PowerShell sebagai
Administrator pada akun Windows pemilik Podman machine, lalu:

```powershell
.\deploy\windows\register-startup-task.ps1
```

Task memanggil `start-portal.ps1`, yang menyalakan machine, menunggu
`podman info`, menjalankan compose secara idempoten, dan mencatat log ke
`C:\hc-portal\logs`. Uji sekali dengan **me-restart server tanpa login**.

## IIS HTTPS reverse proxy

1. Pasang sertifikat dan binding HTTPS IIS untuk nama DNS portal.
2. Instal URL Rewrite dan ARR.
3. Salin `deploy\windows\web.config` ke root situs IIS dan ubah host contoh
   `hc.example.internal` ke nama DNS yang sebenarnya.
4. Jalankan perintah `appcmd` yang tercantum sebagai komentar di `web.config`.
5. Jangan expose port 3000 ke jaringan; arahkan IIS ke `http://127.0.0.1:3000`.
6. `APP_BASE_URL=https://<nama-portal>`: cookie sesi lalu bertanda `Secure`,
   setiap link persetujuan di email memakai alamat itu, dan portal hanya
   menerima POST yang `Origin`-nya alamat itu.

## Verifikasi dan aktifkan penulisan

- Login lalu buka **Status sistem** (`/status`, peran admin/operator). Semua
  pemeriksaan hanya membaca: LDAPS, bind akun layanan + base DN, OU kelola,
  OU karantina, OU per divisi, group CISO, mode tulis, sinkron, SMTP, antrean
  email, 2FA, akun demo, cek ulang sesi, HTTPS.
- Setelah pemilik AD menyetujui akun service dan OU, set
  `AD_LDAP_WRITE_ENABLED=true` di `.env.production.local`, lalu
  `podman compose -f compose.production.yml up -d`.
- Layanan AD tetap menolak menulis di luar `AD_MANAGED_OUS`, dan hanya
  mengubah group yang ada di katalog `AD_ACCESS_GROUP_*`.

## Login, 2FA, dan catatan keamanan

- `LOGIN_2FA=on` (bawaan): login HC = kata sandi AD + kode authenticator. Login
  pertama menampilkan QR. Kunci 2FA tersimpan terenkripsi di
  `/app/data/hc-mfa.json` dengan `OUTBOX_ENCRYPTION_KEY` — bila kunci itu
  berubah, semua HP harus didaftarkan ulang.
- HP hilang/ganti: `podman exec hc-portal python scripts/mfa_reset.py <username>`;
  daftar yang terdaftar: `... mfa_reset.py --list`.
- Kode selalu salah untuk semua orang = jam Podman machine melenceng:
  `podman machine stop`, `wsl --shutdown`, `podman machine start`.
- Lima kali salah dalam 15 menit mengunci akun itu di portal 15 menit.
- Sesi dicocokkan dengan AD tiap `SESSION_RECHECK_MINUTES` (bawaan 5).
- Catatan login di `/app/data/hc-security-log.jsonl` dan di **Status sistem**.

## Backup dan restore

`backup-data.ps1` menghentikan portal sementara agar ekspor volume konsisten,
menghasilkan arsip tar bertimestamp di folder `backups` (ubah dengan
`-BackupPath`), menyalakan kembali portal, dan menghapus arsip lebih lama dari
30 hari:

```powershell
.\deploy\windows\backup-data.ps1
```

Restore:

1. `podman compose -f compose.production.yml down`.
2. Simpan salinan kondisi saat ini terlebih dahulu.
3. `podman volume rm hc-portal-data` lalu `podman volume create hc-portal-data`.
4. `podman volume import hc-portal-data C:\hc-portal\backups\hc-portal-data-<timestamp>.tar`.
5. `podman compose -f compose.production.yml up -d`, lalu periksa login,
   **Status sistem**, data, dan antrean.

Backup berisi data pegawai dan sesi; lindungi ACL dan salin ke media terpisah.
Ingat juga `.env.production.local`: tanpa `OUTBOX_ENCRYPTION_KEY` yang sama,
data 2FA dan antrean email di backup tidak bisa dibuka.

## Update aplikasi

```powershell
.\deploy\windows\update-portal.ps1 -ImageArchive C:\hc-portal\hc-images.tar.gz
```

Tanpa `-ImageArchive`, skrip membangun image dari folder ini (perlu
internet). Bila folder aplikasi adalah checkout git, skrip menjalankan
`git pull` dulu. Jangan menimpa `.env.production.local`, `certs`, `logs`,
`backups`, atau volume `hc-portal-data`. Pastikan backup terbaru sebelum update.

Semua skrip di folder ini ditulis untuk Windows PowerShell 5.1 — versi yang
dipakai Task Scheduler — dan memanggil `podman` lewat `podman-common.ps1`.

## Batas deployment

Hanya satu portal boleh berjalan terhadap volume `hc-portal-data`. Worker,
outbox, dan penguncian store JSON berada dalam satu proses; dua instance dapat
memproses pekerjaan yang sama atau mengirim email persetujuan dua kali. Jangan
scale service, menjalankan container kedua untuk update, atau menghubungkan
volume yang sama dari host lain.
