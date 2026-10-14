[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$PythonEmbedZip,
  [Parameter(Mandatory)][string]$JavaRuntimeZip,
  [Parameter(Mandatory)][string]$WinSWExe,
  [Parameter(Mandatory)][string]$Uv,
  [Parameter(Mandatory)][string]$Maven
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$build = Join-Path $PSScriptRoot 'build'
if (-not ([IO.Path]::GetFullPath($build).StartsWith(([IO.Path]::GetFullPath($repo) + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase))) { throw 'Lokasi build di luar repository.' }
if (Test-Path $build) { Remove-Item -LiteralPath $build -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $build 'runtime\python\Lib\site-packages') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $build 'runtime\java') | Out-Null
Expand-Archive -LiteralPath $PythonEmbedZip -DestinationPath (Join-Path $build 'runtime\python')
$pth = Get-ChildItem (Join-Path $build 'runtime\python') -Filter '*._pth' | Select-Object -First 1
if (-not $pth) { throw 'Python embeddable distribution tidak ditemukan.' }
$stdlib = Get-ChildItem (Join-Path $build 'runtime\python') -Filter 'python*.zip' | Select-Object -First 1
if (-not $stdlib) { throw 'Arsip pustaka standar Python tidak ditemukan.' }
[IO.File]::WriteAllText($pth.FullName, "$($stdlib.Name)`r`n.`r`nLib\site-packages`r`n..\..\portal`r`nimport site`r`n", (New-Object Text.UTF8Encoding($false)))
$embeddedPython = Join-Path $build 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $embeddedPython)) { throw 'python.exe tidak ditemukan dalam Python embeddable ZIP.' }
$oldErrorActionPreference = $ErrorActionPreference
try {
  $ErrorActionPreference = 'Continue' # native tools may write warnings to stderr
  & $Uv pip install --quiet --python $embeddedPython --requirement (Join-Path $repo 'portal\requirements.txt') --target (Join-Path $build 'runtime\python\Lib\site-packages')
  $uvExitCode = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $oldErrorActionPreference
}
if ($uvExitCode -ne 0) { throw 'Pemasangan dependensi Python gagal.' }
Expand-Archive -LiteralPath $JavaRuntimeZip -DestinationPath (Join-Path $build 'runtime\java')
$javaRoot = Join-Path $build 'runtime\java'
if (-not (Test-Path (Join-Path $javaRoot 'bin\java.exe'))) {
  $nested = @(Get-ChildItem -LiteralPath $javaRoot -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'bin\java.exe') })
  if ($nested.Count -ne 1) { throw 'Arsip Java harus berisi satu runtime dengan bin/java.exe.' }
  Get-ChildItem -LiteralPath $nested[0].FullName -Force | Move-Item -Destination $javaRoot
  Remove-Item -LiteralPath $nested[0].FullName
}
try {
  $ErrorActionPreference = 'Continue'
  & $Maven -B -f (Join-Path $repo 'ad-service\pom.xml') clean package
  $mavenExitCode = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $oldErrorActionPreference
}
if ($mavenExitCode -ne 0) { throw 'Build Java gagal.' }
Copy-Item (Join-Path $repo 'ad-service\target\ad-service.jar') (Join-Path $build 'ad-service.jar')
Copy-Item -LiteralPath $WinSWExe -Destination (Join-Path $build 'WinSW.exe')
New-Item -ItemType Directory -Force (Join-Path $build 'portal') | Out-Null
Copy-Item (Join-Path $repo 'portal\app') (Join-Path $build 'portal\app') -Recurse
foreach ($name in @('common.ps1','validate-config.ps1','preflight.ps1','finalize-install.ps1','start-portal.ps1','start-ad-service.ps1','install-services.ps1','uninstall-services.ps1','install-iis.ps1','health-check.ps1','backup-data.ps1','restore-data.ps1','update-application.ps1','create-ad-ous.ps1','portal-service.xml','ad-service.xml')) {
  Copy-Item (Join-Path $PSScriptRoot $name) (Join-Path $build $name)
}
Copy-Item (Join-Path $repo 'PANDUAN-AD-OU.md') (Join-Path $build 'PANDUAN-AD-OU.md')
Copy-Item (Join-Path $PSScriptRoot 'README.md') (Join-Path $build 'PANDUAN-INSTALASI.md')
Write-Host "Paket siap di $build. Jalankan ISCC.exe installer.iss."
