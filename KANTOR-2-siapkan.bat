@echo off
REM ---------------------------------------------------------------------------
REM  PC KANTOR, langkah 2: menyiapkan mesin Podman, memuat image dari
REM  hc-images.tar.gz, dan membuat .env.production.local (dua kunci rahasia
REM  sudah terisi). Boleh dijalankan ulang kapan saja.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\kantor-siapkan.ps1"
echo.
pause
