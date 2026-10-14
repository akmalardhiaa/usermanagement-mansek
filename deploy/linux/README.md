# Menjalankan HC Portal pada Linux server

Target akhir kantor: VM Linux dengan Podman rootless, dua container Linux,
reverse proxy HTTPS dan Active Directory yang sudah ada. Jalur ini tidak
memerlukan Hyper-V, WSL, Docker, IIS, Python, Java atau Maven di server.
Skrip `deploy/windows/*.ps1` adalah jalur Windows lama, kecuali skrip pembuat
paket yang dijalankan pada laptop Windows.

## Permintaan kepada tim server dan AD

- Berikan distro/versi Linux yang didukung IT, akun operator tetap yang boleh
  menjalankan Podman rootless, serta izin layanan user systemd tetap hidup
  setelah logout (`loginctl enable-linger NAMA_AKUN`, dijalankan admin).
- Usulan awal VM: 4 vCPU, RAM 8 GiB, disk 80 GiB. Sediakan partisi/ruang
  persisten untuk volume Podman dan backup terpisah; angka ini perlu diuji beban.
- Pasang Podman, penyedia `podman compose` (misalnya `podman-compose` yang
  disetujui IT), reverse proxy Nginx/Apache/Caddy, DNS portal dan sertifikat
  HTTPS. Atur `PODMAN_COMPOSE_PROVIDER` ke binary penyedia yang disetujui,
  misalnya `/usr/bin/podman-compose`; konfirmasikan dukungan format Compose.
- Izinkan resolusi DNS dan akses dari **container** ke DC LDAPS TCP 636 dan
  relay SMTP/Graph. Hanya reverse proxy port 443 yang dibuka ke LAN;
  portal terikat `127.0.0.1:3000`, layanan Java tidak dipublikasikan.
- Tim AD memberikan FQDN DC, base DN, CA publik PEM, akun layanan dengan
  delegasi OU terbatas, DN OU/group, dan OU uji. Ikuti
  [panduan AD/OU](../../PANDUAN-AD-OU.md). Kirim password lewat vault kantor.
- Tentukan backup volume, retensi, salinan di luar VM, monitoring, prosedur
  restart dan uji restore. Jangan mengandalkan snapshot VM saja.

## Paket dan konfigurasi

Di laptop Windows, buat paket dengan:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\package-office.ps1 -Destination D:\kirim-ke-kantor
```

Paket **baru** dari perintah itu memuat `hc-images.tar`, hash,
`compose.production.yml`, panduan Linux dan template env. Paket lama yang
dibuat sebelum perubahan ini belum memuat berkas Linux; salin ketiganya dari
repo bila memakai paket lama. Di Linux, ekstrak folder `hc-portal` dari ZIP,
masuk ke folder itu, lalu periksa hash sebelum memuat image:

```sh
sha256sum -c hc-images.sha256.txt
podman load -i hc-images.tar
podman image exists localhost/hc-portal:latest
podman image exists localhost/hc-ad-service:latest
```

Jalankan sebagai akun operator yang sama untuk seluruh langkah. Jika server
boleh build dan mendapat akses registry/dependency, source checkout bisa
dibangun dengan `podman compose -f compose.production.yml build`; jalur arsip
lebih sederhana untuk server tanpa akses build.

Dalam folder yang memuat `compose.production.yml`, buat `.env.production.local`
dari template. Isi seluruh nilai produksi, terutama AD/OU, SMTP/Graph,
`APP_BASE_URL=https://nama-portal`, `AD_SERVICE_TOKEN`, dan
`OUTBOX_ENCRYPTION_KEY`. Simpan CA **publik** sebagai
`certs/corp-root-ca.pem`. Bila relay SMTP memakai CA kantor, isi
`SMTP_CA_CERT_PATH=/run/certs/corp-root-ca.pem` agar portal juga memakai CA itu.
Jangan taruh private key DC di folder ini. Batasi akses folder dan env ke akun
operator/admin (`chmod 700` folder rahasia; `chmod 600 .env.production.local`).
CA harus bisa dibaca UID container 10001 dan 10002; `chmod 644` aman untuk
CA publik. Pada host SELinux, Compose memakai label shared `:z` pada salinan CA.

Periksa file sebelum start:

```sh
podman compose -f compose.production.yml config
```

Perintah ini dapat **menampilkan rahasia env ke terminal/log**, jadi jalankan
di sesi operator privat dan jangan bagikan output. Cek hasilnya, termasuk dua
mount CA, URL, dan volume data. Pastikan tidak ada nilai `<placeholder>`.

## Start dan verifikasi

```sh
podman compose -f compose.production.yml up -d --no-build
podman compose -f compose.production.yml ps
curl -fsS http://127.0.0.1:3000/healthz
podman exec hc-portal python /app/scripts/healthcheck.py --ad
```

`healthz` dan pemeriksaan Java hanya membuktikan proses menjawab. Buka
**Status sistem** lewat HTTPS untuk menguji bind AD, sertifikat, OU dan email.
Biarkan `AD_LDAP_WRITE_ENABLED=false` sampai tes baca selesai; aktifkan hanya
untuk OU uji dan jalankan ulang Compose setelah konfigurasi berubah.
Uji onboarding dummy, approval, OU tujuan, group, password awal, movement,
termination dan reboot sebelum membuka OU produksi.

Reverse proxy harus meneruskan HTTPS ke `http://127.0.0.1:3000` dengan header
`Host`, `X-Forwarded-Proto`, dan `X-Forwarded-For` yang benar. Gunakan
sertifikat yang dipercaya PC kantor. `APP_BASE_URL` harus sama dengan URL
HTTPS publik; tautan approval email berasal dari nilai ini.
Pada Nginx yang langsung menerima trafik pengguna, bagian proxy dapat seperti
ini (di dalam `server` HTTPS milik IT):

```nginx
location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;
}
```

Jangan meneruskan `X-Forwarded-For` mentah dari klien: aplikasi memakai header
itu untuk batas percobaan login. Jika ada load balancer terpercaya di depan
Nginx, tim jaringan perlu mengatur daftar proxy tepercaya dan alamat klien nyata.

Untuk startup otomatis, tim IT dapat membuat unit systemd **user** di
`~/.config/systemd/user/hc-portal.service` milik operator. Contoh bila folder
aplikasi `/home/hcportal/hc-portal` dan penyedia Compose di
`/usr/bin/podman-compose` (sesuaikan path sebenarnya):

```ini
[Unit]
Description=HC Portal

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/hcportal/hc-portal
Environment=PODMAN_COMPOSE_PROVIDER=/usr/bin/podman-compose
ExecStart=/usr/bin/podman compose -f compose.production.yml up -d --no-build
ExecStop=/usr/bin/podman compose -f compose.production.yml stop
TimeoutStartSec=180

[Install]
WantedBy=default.target
```

Sebagai operator: `systemctl --user daemon-reload` lalu
`systemctl --user enable --now hc-portal.service`. IT mengaktifkan linger untuk
akun itu dan wajib menguji boot tanpa login. `stop` mempertahankan volume;
jangan memakai `down -v` untuk layanan ini.

## Backup dan update

Data aplikasi ada di volume Podman `hc-portal-data`. Untuk backup konsisten,
hentikan portal sebentar, ekspor volume, lalu hidupkan kembali; jadwalkan pada
maintenance window dan simpan salinan di luar VM. Contoh manual sebagai akun
operator, dengan folder backup yang sudah disediakan IT:

```sh
podman stop hc-portal
podman volume export hc-portal-data -o /lokasi-backup/hc-portal-data.tar
podman start hc-portal
```

Periksa exit code setiap perintah; jika ekspor gagal, tetap pulihkan layanan
secara terkendali. Backup `.env.production.local` (terutama kunci enkripsi),
CA dan konfigurasi proxy secara terpisah melalui vault/backup aman. Arsip
volume tanpa kunci enkripsi tidak cukup untuk memulihkan data 2FA/email.
Uji restore pada VM terpisah dengan **volume kosong** dan image versi cocok.
Untuk update, simpan arsip lama untuk rollback, backup dulu, `podman load`
arsip baru, kemudian `podman compose -f compose.production.yml up -d --no-build
--force-recreate`. Verifikasi HTTPS, AD dan data setelahnya.

Catatan: kode saat ini belum menetapkan password awal AD (`unicodePwd`), jadi
admin AD perlu prosedur reset password sebelum user bisa login. Rincian ada
pada panduan AD/OU.
