# Read-only prerequisite check. IT installs approved software and enables Hyper-V.
$ErrorActionPreference = 'Stop'
$os = Get-CimInstance Win32_OperatingSystem
Write-Host "OS: $($os.Caption), build $($os.BuildNumber), $($os.OSArchitecture)"
Write-Host 'Target: Windows Server 2022 x64, Podman 6+, provider Hyper-V (wajib uji kompatibilitas IT).'
Write-Host 'VM kantor memerlukan nested virtualization dari administrator host.'
if ($os.Caption -notmatch 'Windows Server 2022' -or $os.OSArchitecture -notmatch '64') {
    throw 'OS berbeda dari target yang disiapkan. Konfirmasikan kompatibilitas OS dan Podman dengan IT.'
}
try {
    $feature = Get-WindowsFeature -Name Hyper-V
    $management = Get-WindowsFeature -Name Hyper-V-PowerShell
    Write-Host "Hyper-V: $($feature.InstallState); PowerShell: $($management.InstallState)"
    if (-not $feature.Installed -or -not $management.Installed) { throw 'Role Hyper-V dan modul PowerShell wajib terpasang.' }
} catch {
    Write-Host 'Minta IT memeriksa/menyalakan Hyper-V dari PowerShell Administrator dan melakukan restart:'
    Write-Host 'Install-WindowsFeature -Name Hyper-V -IncludeManagementTools'
    throw 'Pemeriksaan Hyper-V gagal. Jalankan pengecekan sebagai Administrator; lihat panduan kantor.'
}
Write-Warning 'Role terpasang belum membuktikan nested virtualization/Podman bekerja. IT wajib menguji init, start, jaringan dan reboot pada Server 2022 sebelum produksi.'
$computer = Get-CimInstance Win32_ComputerSystem
Write-Host ("RAM VM Windows: {0:N1} GiB. Rekomendasi awal: 8 GiB RAM, 4 vCPU, disk 80 GiB." -f ($computer.TotalPhysicalMemory / 1GB))
Write-Host 'Pasang MSI Podman 6+ x64 resmi yang disetujui IT; PILIH HYPER-V pada installer.'
Write-Host 'Tidak perlu Docker Desktop, Docker Engine, Docker Compose, Python atau Java di Windows.'
Write-Host 'Gunakan satu akun operator untuk setup/startup/backup, anggota Hyper-V Administrators.'
Write-Host 'Pada Podman 6+, IT dapat menjalankan: podman system hyperv-prep (untuk akun operator yang dituju).'
Write-Host 'Buka terminal baru setelah instalasi. Lanjutkan deploy/windows/kantor-siapkan.ps1 sebagai akun operator.'
