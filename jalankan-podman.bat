@echo off
REM ---------------------------------------------------------------------------
REM  Menjalankan portal (Python) dan layanan AD (Java) dengan Podman, untuk
REM  laptop atau PC uji. Klik dua kali berkas ini. Kantor memakai
REM  compose.production.yml - lihat PANDUAN-PC-KANTOR.md.
REM
REM  Podman dipilih menggantikan Docker Desktop karena lisensinya: Docker
REM  Desktop berbayar untuk perusahaan besar, Podman tidak.
REM
REM  KENAPA LEWAT "wsl -d" DAN BUKAN "podman compose" LANGSUNG.
REM
REM  Di Windows, Podman menjalankan containernya di dalam sebuah mesin WSL, dan
REM  perintah podman dari sisi Windows menyambung ke mesin itu lewat SSH. Pada
REM  komputer yang pernah dipasangi Docker Desktop, jembatan SSH itu bisa gagal
REM  menyala:
REM
REM    CreateFile \\.\pipe\podman-machine-default: All pipe instances are busy
REM    Error: machine did not transition into running state: ssh error
REM
REM  Padahal podman DI DALAM mesinnya sehat. Jadi skrip ini memanggilnya di
REM  sana langsung dan melewati jembatan yang rusak itu.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "MESIN=podman-machine-default"

set "PODMAN="
for /f "delims=" %%p in ('where podman 2^>nul') do set "PODMAN=%%p"
if not defined PODMAN if exist "%ProgramFiles%\RedHat\Podman\podman.exe" set "PODMAN=%ProgramFiles%\RedHat\Podman\podman.exe"

if not defined PODMAN (
  echo.
  echo  Podman belum terpasang. Pasang sekali saja:
  echo.
  echo      winget install -e --id RedHat.Podman
  echo.
  echo  Lalu jalankan berkas ini lagi.
  pause
  exit /b 1
)

echo  [1/5] Memastikan berkas pengaturan .env.dev.local ada...
if not exist ".env.dev.local" (
  REM Dibuat dari .env.example dengan dua rahasia acak baru. Isinya tidak
  REM ditampilkan dan tidak pernah dikirim ke mana pun.
  powershell -NoProfile -Command "$g=[Security.Cryptography.RandomNumberGenerator]::Create(); $b=New-Object byte[] 32; $g.GetBytes($b); $key=[Convert]::ToBase64String($b); $g.GetBytes($b); $token=[Convert]::ToBase64String($b).TrimEnd('=').Replace('+','-').Replace('/','_'); $text=(Get-Content -Raw .env.example) -replace '(?m)^AD_SERVICE_TOKEN=.*$',('AD_SERVICE_TOKEN='+$token) -replace '(?m)^OUTBOX_ENCRYPTION_KEY=.*$',('OUTBOX_ENCRYPTION_KEY='+$key); [IO.File]::WriteAllText((Join-Path (Get-Location) '.env.dev.local'), $text)"
  if not exist ".env.dev.local" (
    echo  .env.dev.local gagal dibuat. Salin .env.example menjadi .env.dev.local
    echo  lalu isi AD_SERVICE_TOKEN dan OUTBOX_ENCRYPTION_KEY sendiri.
    pause
    exit /b 1
  )
  echo        Dibuat dengan rahasia acak baru ^(mode demo, tanpa AD^).
)

echo  [2/5] Memastikan mesin Podman ada...
wsl -d %MESIN% -u root -- true >nul 2>&1
if not %errorlevel%==0 (
  echo        Belum ada, membuat ^(sekali saja, beberapa menit^)...
  "%PODMAN%" machine init
  if errorlevel 1 (
    echo  Pembuatan mesin gagal. Pastikan WSL2 aktif: wsl --install
    pause
    exit /b 1
  )
)

echo  [3/5] Menyalakan mesinnya...
wsl -d %MESIN% -u root -- true >nul 2>&1
if not %errorlevel%==0 (
  echo.
  echo  Mesin Podman tidak bisa dinyalakan. Coba:
  echo      wsl --shutdown
  echo  lalu jalankan berkas ini lagi.
  pause
  exit /b 1
)

REM MTU 1280: di sebagian jaringan paket besar dibuang diam-diam dan TLS macet
REM (email, LDAPS, unduhan image). Aman di jaringan mana pun.
wsl -d %MESIN% -u root -- ip link set dev eth0 mtu 1280 >nul 2>&1

REM Letak folder ini seperti yang dilihat dari dalam mesin.
for /f "delims=" %%p in ('wsl -d %MESIN% -u root -e wslpath -a "%CD%"') do set "DIRLINUX=%%p"

echo  [4/5] Memastikan podman-compose ada di dalam mesin...
wsl -d %MESIN% -u root -- sh -lc "command -v podman-compose >/dev/null 2>&1" >nul 2>&1
if not %errorlevel%==0 (
  echo        Memasang podman-compose ^(sekali saja^)...
  wsl -d %MESIN% -u root -- sh -lc "dnf install -y podman-compose >/dev/null 2>&1"
  wsl -d %MESIN% -u root -- sh -lc "command -v podman-compose >/dev/null 2>&1"
  if not %errorlevel%==0 (
    echo  Pemasangan podman-compose gagal ^(perlu koneksi internet sekali^).
    pause
    exit /b 1
  )
)

echo  [5/5] Membangun dan menjalankan portal + layanan AD...
echo        ^(Pertama kali beberapa menit: Python dan Java diunduh sekali.^)
echo.
wsl -d %MESIN% -u root -- sh -lc "cd '%DIRLINUX%' && PODMAN_IGNORE_CGROUPSV1_WARNING=1 podman-compose up -d --build"
if errorlevel 1 (
  echo.
  echo  Portal gagal dijalankan. Lihat pesan di atas.
  pause
  exit /b 1
)

echo.
echo  ==========================================================
echo   Portal berjalan di  http://localhost:3000
echo  ==========================================================
echo.
echo   Login demo:  admin / admin12345   - selama AD belum disambungkan
echo   Kotak masuk uji:  http://localhost:8026   - semua email portal masuk ke sini
echo.
echo   Melihat log      :  wsl -d %MESIN% -u root -- podman logs -f hc-portal
echo   Menghentikan     :  hentikan-podman.bat
echo.
pause
