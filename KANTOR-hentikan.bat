@echo off
REM  Menghentikan portal dan layanan AD. Data tetap aman di volume hc-portal-data.
setlocal
cd /d "%~dp0"
set "PATH=%PATH%;%ProgramFiles%\RedHat\Podman;%LOCALAPPDATA%\Microsoft\WinGet\Links"
echo  Menghentikan portal ...
podman compose -f compose.production.yml down
echo.
echo  Portal dihentikan. Nyalakan lagi dengan KANTOR-3-jalankan.bat
echo.
pause
