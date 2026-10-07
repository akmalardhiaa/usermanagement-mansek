@echo off
REM ---------------------------------------------------------------------------
REM  Menghentikan portal dan layanan AD yang dijalankan jalankan-podman.bat.
REM  Datanya tetap ada di volume hc-dev-data; menyalakan lagi tidak
REM  menghapus apa pun.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "MESIN=podman-machine-default"

wsl -d %MESIN% -u root -- true >nul 2>&1
if not %errorlevel%==0 (
  echo  Mesin Podman tidak ada. Tidak ada yang perlu dihentikan.
  pause
  exit /b 0
)

for /f "delims=" %%p in ('wsl -d %MESIN% -u root -e wslpath -a "%CD%"') do set "DIRLINUX=%%p"

echo  Menghentikan portal...
wsl -d %MESIN% -u root -- sh -lc "cd '%DIRLINUX%' && PODMAN_IGNORE_CGROUPSV1_WARNING=1 podman-compose down"

echo.
echo  Portal dihentikan. Datanya tetap di volume hc-dev-data.
echo.
pause
