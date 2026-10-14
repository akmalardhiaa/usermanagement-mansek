# Legacy Windows helper: find Podman and Compose from PowerShell.
# opened before they were installed, and say plainly what is missing.

. (Join-Path $PSScriptRoot "podman-common.ps1")

$script:AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Use-PodmanPath {
    # A window opened before the install does not see the new PATH yet.
    foreach ($dir in @("$env:ProgramFiles\RedHat\Podman", "$env:LOCALAPPDATA\Microsoft\WinGet\Links")) {
        if ((Test-Path -LiteralPath $dir) -and ($env:Path -split ";") -notcontains $dir) {
            $env:Path = "$env:Path;$dir"
        }
    }
    if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
        throw 'Podman belum terpasang. Minta IT memasang Podman atau jalankan deploy/windows/kantor-prasyarat.ps1.'
    }
}

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host " $Text" -ForegroundColor Yellow
}

function Write-Ok {
    param([string]$Text)
    Write-Host "   OK  $Text" -ForegroundColor Green
}

function Write-Todo {
    param([string]$Text)
    Write-Host "   !!  $Text" -ForegroundColor Red
}

# Keys in .env.production.local that still hold a <placeholder>; names only, never values.
function Get-UnfilledSettings {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    @(Get-Content -LiteralPath $Path | Where-Object { $_ -match '^\s*([A-Z0-9_]+)=.*<[^>]+>' } |
        ForEach-Object { ($_ -split "=", 2)[0].Trim() })
}
