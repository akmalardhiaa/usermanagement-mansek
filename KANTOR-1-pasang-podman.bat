@echo off
REM ---------------------------------------------------------------------------
REM  PC KANTOR, langkah 1 (sekali saja): memasang WSL2, Podman, dan Compose.
REM  Klik kanan berkas ini lalu pilih "Run as administrator". Perlu internet.
REM ---------------------------------------------------------------------------
setlocal
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Berkas ini perlu hak administrator.
  echo  Tutup jendela ini, klik kanan KANTOR-1-pasang-podman.bat,
  echo  lalu pilih "Run as administrator".
  echo.
  pause
  exit /b 1
)

echo.
echo  [1/3] Memasang WSL2 ...
wsl --install --no-distribution
echo.
echo  [2/3] Memasang Podman ...
winget install -e --id RedHat.Podman --accept-package-agreements --accept-source-agreements
echo.
echo  [3/3] Memasang Compose ...
winget install -e --id Docker.DockerCompose --accept-package-agreements --accept-source-agreements

echo.
echo  ==========================================================
echo   Selesai. RESTART PC bila diminta di atas.
echo   Setelah itu klik dua kali KANTOR-2-siapkan.bat
echo   (biasa saja, bukan Run as administrator).
echo  ==========================================================
echo.
pause
