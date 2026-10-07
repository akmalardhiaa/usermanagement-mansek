# Panduan memasang portal di PC kantor

Panduan ini untuk memasang **HC User Management** di PC/server kantor yang akan
dipakai sungguhan: tersambung ke Active Directory, mengirim email persetujuan,
dan login HC memakai verifikasi 2 langkah (2FA).

Ikuti berurutan. Setiap bagian ditutup dengan **cek** — jangan lanjut sebelum
ceknya berhasil. Detail teknis tambahan ada di
[deploy/windows/README.md](deploy/windows/README.md).

```
 Staf HC (browser)              Manajer & tim CISO (email)
        │ https://<nama-portal>          │ klik Setujui / Tolak
        ▼                                ▼
   IIS (HTTPS) di PC kantor ──► portal  (container Python, 127.0.0.1:3000)
                                   │  ├──► server email kantor (SMTP)
                                   ▼  │
                       layanan AD (container Java, tanpa port)
                                   └──► Active Directory (LDAPS 636)
```

Portal terdiri dari **dua container**:

| Container | Bahasa | Tugasnya |
|---|---|---|
| `hc-portal` | Python | Halaman untuk HC, pengajuan, email persetujuan, 2FA, data |
| `hc-ad-service` | Java | Satu-satunya yang bicara ke Active Directory (login, baca, tulis OU) |

Keduanya dijalankan bersama oleh `compose.production.yml`. Layanan AD tidak
punya port ke luar: hanya portal yang bisa menghubunginya, dengan kunci
bersama `AD_SERVICE_TOKEN`.

---

## 0. Siapkan dulu

### Yang diminta ke tim IT

| Yang diminta | Diisi ke |
|---|---|
| Nama/alamat domain controller, LDAPS port **636** aktif dan bisa dijangkau dari PC ini | `AD_LDAP_URL` |
| Sertifikat CA perusahaan (file `.cer`/`.pem`) | `certs\corp-root-ca.pem` |
| **Akun layanan** + kata sandinya, didelegasikan hanya ke OU karyawan dan OU karantina (bukan Domain Admins) | `AD_BIND_DN`, `AD_BIND_PASSWORD` |
| DN OU karyawan, OU per divisi, OU karantina | `AD_OU_*`, `AD_DEPARTMENT_*`, `AD_QUARANTINE_OU`, `AD_MANAGED_OUS` |
| DN group HC (yang boleh masuk portal), group admin, group tim CISO, group akses | `AD_GROUP_*`, `CISO_APPROVER_GROUP`, `AD_ACCESS_GROUP_*` |
| Server email (SMTP): host, port, login atau relay tanpa login, alamat pengirim | `SMTP_*` |
| Nama DNS untuk portal + sertifikat HTTPS-nya (untuk IIS) | `APP_BASE_URL` |
| Hak **administrator** di PC ini (untuk memasang WSL2, Podman, IIS) | — |
| Apakah akun baru boleh aktif tanpa password? (lihat bagian 10) | — |

### Yang Anda bawa ke PC kantor

Di laptop, klik dua kali **`siapkan-pindah.bat`** dan isi tujuan (misalnya
`D:\kirim-ke-kantor`). Hasilnya:

| Berkas | Isinya |
|---|---|
| `hc-images.tar.gz` (±200 MB) | Kedua container yang sudah dirakit. PC kantor tidak perlu internet, Python, atau Java. |
| `hc-images.sha256.txt` | Sidik jari, untuk memastikan berkasnya utuh setelah dikirim |
| folder `hc-portal\` | Berkas proyek: compose, skrip, panduan, template pengaturan |

Kirim lewat jalur yang diizinkan kantor (tanyakan ke pembimbing/IT).
Kode juga bisa diambil di PC kantor dari GitHub (**Code → Download ZIP**);
yang wajib dikirim hanya `hc-images.tar.gz`.

**JANGAN** bawa folder `data`, `.env.dev.local`, `.env.local`, atau
`.env.podman`. Isinya data uji dan kata sandi laptop. Di kantor portal mulai
dengan data kosong dan langsung terisi dari AD. `siapkan-pindah.bat` sudah
tidak menyertakannya.

---

## 1. Pasang WSL2 dan Podman (sekali saja)

1. Pastikan virtualisasi aktif: **Task Manager → Performance → CPU →
   Virtualization: Enabled**. Kalau *Disabled*, minta IT menyalakannya di BIOS.
2. Buka **PowerShell sebagai Administrator**:

   ```powershell
   wsl --install --no-distribution
   ```

   Restart PC bila diminta.
3. Pasang Podman dan compose (keduanya gratis untuk perusahaan):

   ```powershell
   winget install -e --id RedHat.Podman
   winget install -e --id Docker.DockerCompose
   ```

   Tutup lalu buka lagi PowerShell (biasa, bukan Administrator).
4. Buat dan nyalakan mesin Podman. Dua container butuh memori lebih dari
   bawaan, jadi beri 4 GB:

   ```powershell
   podman machine init --memory 4096
   podman machine start
   ```

**Cek:** `podman info` menampilkan informasi tanpa error.

> Pakai **satu akun Windows yang sama** untuk semua langkah berikutnya. Mesin
> Podman milik akun yang membuatnya.

---

## 2. Taruh berkas di `C:\hc-portal`

Salin folder `hc-portal` dari paket menjadi `C:\hc-portal` (di dalamnya
langsung ada `compose.production.yml`, `deploy`, `portal`, `ad-service`, dst).

```powershell
Set-Location C:\hc-portal
```

**Cek:** `Test-Path .\compose.production.yml` menjawab `True`.

---

## 3. Muat image

Taruh `hc-images.tar.gz` di `C:\hc-portal`, periksa sidik jarinya, lalu muat:

```powershell
(Get-FileHash .\hc-images.tar.gz -Algorithm SHA256).Hash.ToLower()
podman load -i .\hc-images.tar.gz
```

**Cek:** hash yang tampil sama dengan isi `hc-images.sha256.txt`, dan
`podman images` menampilkan `hc-portal` dan `hc-ad-service`, keduanya `latest`.

> Tidak punya berkas image tapi internet lancar? Lewati langkah ini; di
> bagian 6 jalankan dulu
> `podman compose -f compose.production.yml build`.

---

## 4. Sertifikat CA

1. Simpan sertifikat CA dari IT sebagai `C:\hc-portal\certs\corp-root-ca.pem`
   (buat folder `certs` bila belum ada).
2. Kalau yang diberikan file `.cer`: buka `certlm.msc` → temukan CA →
   **All Tasks → Export → Base-64 encoded X.509** → simpan dengan nama di atas.

**Cek:** file `certs\corp-root-ca.pem` dibuka di Notepad diawali
`-----BEGIN CERTIFICATE-----`.

---

## 5. Isi `.env.production.local`

```powershell
Copy-Item deploy\windows\env.production.example .env.production.local
notepad .env.production.local
```

Ganti semua `<...>`. Dua rahasia dibuat sekali di PC ini — jalankan perintah
ini **dua kali**, hasil pertama untuk `AD_SERVICE_TOKEN`, hasil kedua untuk
`OUTBOX_ENCRYPTION_KEY`:

```powershell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)
```

Yang perlu diperhatikan:

| Kunci | Isi |
|---|---|
| `APP_BASE_URL` | **Sementara** `http://localhost:3000` untuk uji di PC ini. Setelah IIS siap (bagian 8) ganti ke `https://<nama-portal>`. |
| `AD_LDAP_WRITE_ENABLED` | Biarkan `false` dulu (portal hanya membaca AD). |
| `OUTBOX_ENCRYPTION_KEY` | **Jangan pernah diganti** — kunci ini juga mengunci data 2FA. |
| `AD_MANAGED_OUS` | OU yang boleh ditulis: induk OU divisi dan OU karantina. Di luar itu layanan AD menolak menulis. |
| `EMAIL_REDIRECT_TO` | Harus **kosong**. |
| `SMTP_USER` / `SMTP_PASSWORD` | Relay kantor tanpa login: kosongkan **keduanya**, isi `SMTP_SENDER`. |
| `SMTP_TLS` | `required` (wajib terenkripsi). `off` hanya bila IT bilang relay-nya tanpa TLS. |
| `SMTP_CA_CERT_PATH` | Bila relay memakai sertifikat CA kantor: `/run/certs/corp-root-ca.pem`. |
| `LOGIN_2FA` | `on` (disarankan) atau `off`. |

**Simpan salinan isi `.env.production.local`** di tempat aman yang disetujui
kantor — tanpa file ini (terutama kuncinya) data 2FA tidak bisa dibuka.

---

## 6. Nyalakan portal (masih hanya membaca AD)

```powershell
podman compose -f compose.production.yml up -d
```

Buka **http://localhost:3000** di browser PC ini.

**Cek:**
- halaman login muncul;
- `podman ps` menampilkan `hc-portal` (healthy) dan `hc-ad-service`;
- `podman logs hc-portal --tail 20` menampilkan
  `[ad-sync] ... akun dibaca dari AD` tanpa `Traceback`;
- `podman logs hc-ad-service --tail 20` diakhiri `Started AdServiceApplication`.

Kalau portal berhenti dengan pesan `ConfigurationError`, pesannya menyebut
kunci mana di `.env.production.local` yang kurang.

---

## 7. Login pertama, 2FA, dan halaman Status sistem

1. Pasang **Microsoft Authenticator** atau **Google Authenticator** di HP.
2. Login dengan akun AD Anda (anggota group HC/admin).
3. Muncul **kode QR** → di aplikasi pilih *tambah akun* → pindai.
   (Tidak bisa memindai? Ketik kunci yang tertulis di bawah QR.)
4. Masukkan kode 6 digit dari aplikasi → masuk.

Login berikutnya: kata sandi AD, lalu kode 6 digit dari aplikasi.

Buka **http://localhost:3000/status** (menu *Status sistem*, perlu akun group
admin/ops).

**Cek:** semua pemeriksaan **OK**, kecuali:
- *Mode tulis* — "Perlu perhatian" (memang masih baca saja),
- *HTTPS* — "Gagal" sampai bagian 8 selesai.

Bagian **Pengiriman email** harus berbunyi "SMTP ... terhubung" — artinya
portal benar-benar berhasil tersambung dan login ke server email.
*OU per divisi* menyebut divisi yang OU-nya belum ada di AD.

---

## 8. HTTPS lewat IIS (bersama IT)

Supaya staf HC lain bisa membuka portal dan manajer bisa membuka **link
persetujuan di email**, portal harus bisa diakses dari PC lain lewat HTTPS.
Langkahnya (detail di
[README deploy](deploy/windows/README.md#iis-https-reverse-proxy)):

1. IT memasang IIS, **URL Rewrite**, **ARR**, dan sertifikat untuk nama portal.
2. Salin `deploy\windows\web.config` ke root situs IIS, ganti host contohnya.
3. Ubah `APP_BASE_URL=https://<nama-portal>` di `.env.production.local`.
4. Terapkan:

   ```powershell
   podman compose -f compose.production.yml up -d
   ```

**Cek:** dari PC lain, `https://<nama-portal>` membuka halaman login, dan
*Status sistem* → *HTTPS* **OK**. Port 3000 **tidak** dibuka ke jaringan —
hanya IIS.

---

## 9. Nyalakan penulisan ke AD

Setelah pemilik AD setuju:

1. `AD_LDAP_WRITE_ENABLED=true` di `.env.production.local`.
2. `podman compose -f compose.production.yml up -d`
3. Uji satu onboarding sungguhan ke divisi uji coba: HC mengajukan → manajer
   klik **Setujui** di email → tim CISO klik **Setujui** → status **Selesai** →
   akun muncul di OU divisinya.

---

## 10. Satu hal yang hanya bisa dijawab di AD kantor

Portal **tidak pernah menulis password**. Bila domain mewajibkan password
sebelum akun boleh aktif, onboarding berakhir: **akun dibuat tetapi
nonaktif**, dan langkah aktivasi tercatat *Gagal* dengan alasan dari AD. Itu
disengaja. Yang memegang penerbitan password mengisinya, lalu langkah itu
diulang dari halaman pengajuan (tombol **Coba lagi**, untuk akun group
admin/ops). Langkah yang sudah selesai tidak diulang.

---

## 11. Otomatis menyala setelah PC restart

PowerShell **sebagai Administrator**, di `C:\hc-portal`:

```powershell
.\deploy\windows\register-startup-task.ps1
```

Masukkan akun Windows pemilik mesin Podman dan kata sandinya.

**Cek:** restart PC **tanpa login** → setelah beberapa menit portal menjawab
lagi. Kalau tidak, lihat `C:\hc-portal\logs\start-portal.log`.

---

## Pekerjaan rutin

| Untuk | Perintah (di `C:\hc-portal`) |
|---|---|
| Lihat apakah portal jalan | `podman ps` |
| Lihat log | `podman logs hc-portal --tail 50` dan `podman logs hc-ad-service --tail 50` |
| Ubah pengaturan `.env.production.local` | edit, lalu `podman compose -f compose.production.yml up -d` |
| Matikan / nyalakan 2FA | `LOGIN_2FA=off` / `on`, lalu perintah di atas |
| HP staf hilang/ganti (reset 2FA) | `podman exec hc-portal python scripts/mfa_reset.py <username>` |
| Siapa saja yang sudah daftar 2FA | `podman exec hc-portal python scripts/mfa_reset.py --list` |
| Catatan login (berhasil/gagal/terkunci) | halaman *Status sistem*, bagian *Catatan login terakhir* |
| Kirim antrean email / jalankan worker / sinkron AD sekarang | halaman *Status sistem*, bagian *Jalankan sekarang* |
| Backup data | `.\deploy\windows\backup-data.ps1` |
| Pasang versi baru (berkas image baru) | `.\deploy\windows\update-portal.ps1 -ImageArchive C:\hc-portal\hc-images.tar.gz` |
| Hentikan portal | `podman compose -f compose.production.yml down` |

Reset 2FA hanya setelah memastikan orangnya benar: siapa pun yang login
pertama kali dengan kata sandinya setelah reset akan mendaftarkan HP-nya.

---

## Kalau ada masalah

| Gejala | Penyebab & jalan keluar |
|---|---|
| `machine did not transition into running state: ssh error` atau `Cannot connect to Podman` | Mesin menyala setengah. `podman machine stop`, `wsl --shutdown`, `podman machine start`. |
| Unduhan macet / `TLS handshake timeout` saat `machine init`, `load`, atau build | Masalah MTU jaringan. `podman machine ssh -- sudo ip link set dev eth0 mtu 1280`, lalu ulangi. (Hilang setelah mesin restart.) |
| Container berhenti sendiri, `podman machine` mati saat build | Memori mesin kurang. `podman machine stop`, `podman machine set --memory 4096`, `podman machine start`. |
| Kode 2FA **selalu** salah untuk semua orang | Jam mesin Podman melenceng (sering setelah PC sleep). `podman machine stop`, `wsl --shutdown`, `podman machine start`. Pastikan jam HP otomatis. |
| Satu orang kodenya selalu salah | Jam HP-nya tidak otomatis, atau HP lain. Atur jam otomatis; kalau tetap, reset 2FA orang itu. |
| "Akun dikunci sementara" | 5 kali salah dalam 15 menit. Tunggu 15 menit. Ini hanya di portal; login Windows tidak terpengaruh. |
| "Verifikasi 2 langkah akun ini perlu diatur ulang" | `OUTBOX_ENCRYPTION_KEY` berubah. Kembalikan nilai lama, atau reset 2FA semua orang. |
| "Server Active Directory tidak bisa dihubungi" | *Status sistem* dan `podman logs hc-ad-service`: biasanya CA, nama host DC, atau DNS. Uji DNS: `podman exec hc-ad-service getent hosts <nama-host-dc>`. |
| *Status sistem*: "Layanan AD tidak menjawab" | Container `hc-ad-service` mati, atau `AD_SERVICE_TOKEN` tidak sama di kedua container (keduanya membaca berkas yang sama, jadi biasanya karena container lama). `podman compose -f compose.production.yml up -d`. |
| "Portal ini hanya untuk tim Human Capital" | Akun bukan anggota `AD_GROUP_HC`/`AD_GROUP_ADMIN`. Minta IT menambahkan ke group. |
| Email tidak sampai | *Status sistem* → *Pengiriman email* dan *Antrean email* menyebut penyebabnya (login ditolak, relay menolak, TLS). Antrean menunggu sampai pengaturan diperbaiki; tidak ada email yang hilang. |
| Staf yang dinonaktifkan masih bisa masuk | Tidak bisa lebih dari 5 menit (`SESSION_RECHECK_MINUTES`). Cek *Status sistem* → *Cek ulang sesi ke AD*. |
| Portal tidak bisa dibuka dari PC lain | Belum lewat IIS (bagian 8). Jangan membuka port 3000 ke jaringan. |

**Satu portal saja.** Jangan menjalankan portal di dua PC sekaligus untuk
kantor yang sama — dua pengirim email berarti satu email persetujuan terkirim
dua kali.
