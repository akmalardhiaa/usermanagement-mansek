@echo off
REM ---------------------------------------------------------------------------
REM  PC KANTOR, langkah 3: menyalakan portal dan membukanya di browser.
REM  Juga dipakai setiap kali portal perlu dinyalakan ulang, atau setelah
REM  .env.production.local diubah.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\kantor-jalankan.ps1"
echo.
pause
