@echo off
REM ---------------------------------------------------------------------------
REM  Menyiapkan paket untuk PC kantor: dua image container (portal Python dan
REM  layanan AD Java) dalam satu berkas, plus berkas proyek yang diperlukan.
REM  PC kantor tidak perlu internet, Python, Java, atau Maven.
REM
REM  Klik dua kali, lalu isi tujuannya (mis. D:\kirim-ke-kantor) - atau
REM  jalankan dengan tujuan sebagai argumen:  siapkan-pindah.bat D:\kirim
REM
REM  Hasilnya:
REM    <tujuan>\hc-portal-kantor.zip      SATU berkas untuk dikirim, berisi folder:
REM    <tujuan>\hc-portal\                berkas proyek + KANTOR-*.bat, tanpa rahasia dan data,
REM                                       dengan hc-images.tar.gz (kedua image) dan
REM                                       hc-images.sha256.txt (sidik jari) di dalamnya
REM
REM  Berkas .env* dan folder data TIDAK ikut: rahasia kantor dibuat di kantor.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "MESIN=podman-machine-default"

set "TUJUAN=%~1"
if "%TUJUAN%"=="" (
  echo.
  echo  Contoh tujuan:  D:\kirim-ke-kantor
  set /p "TUJUAN=Tujuan paket: "
)
if "%TUJUAN%"=="" (
  echo  Tujuan kosong. Dibatalkan.
  pause
  exit /b 1
)
if not exist "%TUJUAN%" mkdir "%TUJUAN%"

wsl -d %MESIN% -u root -- true >nul 2>&1
if not %errorlevel%==0 (
  echo  Mesin Podman tidak bisa dinyalakan. Jalankan jalankan-podman.bat sekali dulu.
  pause
  exit /b 1
)
for /f "delims=" %%p in ('wsl -d %MESIN% -u root -e wslpath -a "%CD%"') do set "DIRLINUX=%%p"
for /f "delims=" %%p in ('wsl -d %MESIN% -u root -e wslpath -a "%TUJUAN%"') do set "TUJUANLINUX=%%p"

echo  [1/4] Membangun kedua image dari kode di folder ini...
wsl -d %MESIN% -u root -- sh -lc "cd '%DIRLINUX%' && podman build -q -t hc-portal:latest portal && podman build -q -t hc-ad-service:latest ad-service"
if errorlevel 1 (
  echo  Build gagal. Lihat pesan di atas.
  pause
  exit /b 1
)

echo  [2/4] Menyalin berkas proyek ^(tanpa .env, data, dan hasil build^)...
robocopy "." "%TUJUAN%\hc-portal" /MIR /NFL /NDL /NJH /NJS /NP ^
  /XD "%CD%\.git" "%CD%\data" "%CD%\node" "%CD%\node_modules" "%CD%\portal\.venv" "%CD%\ad-service\target" "%CD%\backups" "%CD%\logs" __pycache__ .pytest_cache ^
  /XF ".env*" "*.pem" "*.tar" "*.tar.gz" "*.zip"
if errorlevel 8 (
  echo  Penyalinan gagal. Periksa tujuannya masih punya ruang dan bisa ditulis.
  pause
  exit /b 1
)
REM .env.example memang perlu ikut: templatnya tidak berisi rahasia.
copy /y ".env.example" "%TUJUAN%\hc-portal\.env.example" >nul

REM Di dalam folder hc-portal, supaya di kantor cukup satu folder: KANTOR-2-siapkan.bat
REM menemukannya di sebelahnya.
echo  [3/4] Menyimpan kedua image ke hc-portal\hc-images.tar.gz ^(beberapa menit^)...
wsl -d %MESIN% -u root -- sh -lc "podman save -m hc-portal:latest hc-ad-service:latest | gzip -6 > '%TUJUANLINUX%/hc-portal/hc-images.tar.gz' && cd '%TUJUANLINUX%/hc-portal' && sha256sum hc-images.tar.gz > hc-images.sha256.txt"
if errorlevel 1 (
  echo  Penyimpanan image gagal.
  pause
  exit /b 1
)

echo  [4/4] Memeriksa isi paket dan membuat zip...
set "KURANG="
if not exist "%TUJUAN%\hc-portal\hc-images.tar.gz"                         set "KURANG=%KURANG% hc-images.tar.gz"
if not exist "%TUJUAN%\hc-portal\compose.production.yml"                   set "KURANG=%KURANG% compose.production.yml"
if not exist "%TUJUAN%\hc-portal\deploy\windows\env.production.example"    set "KURANG=%KURANG% env.production.example"
if not exist "%TUJUAN%\hc-portal\KANTOR-2-siapkan.bat"                     set "KURANG=%KURANG% KANTOR-2-siapkan.bat"
if not exist "%TUJUAN%\hc-portal\MULAI-DI-SINI.txt"                        set "KURANG=%KURANG% MULAI-DI-SINI.txt"
if not "%KURANG%"=="" (
  echo.
  echo  KURANG:%KURANG%
  echo  Paket belum lengkap. Jangan dipakai sebelum ini beres.
  pause
  exit /b 1
)
if exist "%TUJUAN%\hc-portal\.env.dev.local" (
  echo  PERINGATAN: .env.dev.local ikut tersalin. Hapus dari paket sebelum dikirim.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Compress-Archive -Path '%TUJUAN%\hc-portal' -DestinationPath '%TUJUAN%\hc-portal-kantor.zip' -CompressionLevel Fastest -Force"
if not exist "%TUJUAN%\hc-portal-kantor.zip" (
  echo  Zip gagal dibuat. Folder %TUJUAN%\hc-portal tetap bisa dikirim apa adanya.
  pause
  exit /b 1
)

echo.
echo  ==========================================================
echo   Paket siap:  %TUJUAN%\hc-portal-kantor.zip
echo  ==========================================================
echo.
type "%TUJUAN%\hc-portal\hc-images.sha256.txt"
echo.
echo   Kirim satu berkas zip itu. Di PC kantor: ekstrak, pindahkan folder
echo   hc-portal ke C:\, lalu ikuti MULAI-DI-SINI.txt di dalamnya.
echo.
pause
