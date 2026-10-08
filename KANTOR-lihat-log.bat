@echo off
REM  Catatan terakhir kedua container, untuk mencari penyebab masalah.
setlocal
cd /d "%~dp0"
set "PATH=%PATH%;%ProgramFiles%\RedHat\Podman;%LOCALAPPDATA%\Microsoft\WinGet\Links"
echo.
echo  ===== Container yang berjalan =====
podman ps -a --format "{{.Names}}   {{.Status}}"
echo.
echo  ===== Portal (hc-portal), 40 baris terakhir =====
podman logs --tail 40 hc-portal
echo.
echo  ===== Layanan AD (hc-ad-service), 25 baris terakhir =====
podman logs --tail 25 hc-ad-service
echo.
echo  Kirim isi jendela ini bila perlu bantuan (tidak berisi kata sandi).
pause
