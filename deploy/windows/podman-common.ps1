# Helpers shared by start-portal.ps1, update-portal.ps1 and backup-data.ps1.
# They dot-source this file; running it on its own does nothing.
#
# Why every podman call goes through Invoke-Native
# ------------------------------------------------
# Task Scheduler runs these scripts with Windows PowerShell 5.1. In 5.1, once a
# native program's stderr is redirected (2>&1, *> $null), every line written
# there becomes an error record, and under $ErrorActionPreference = "Stop" the
# first one ends the script - even when the program exits 0.
#
# podman writes to stderr for things that are not failures: "already running"
# from `machine start`, progress lines from compose, cgroup warnings. Called
# the plain way, the daily backup and every update stopped on their first
# podman line, because both run while the machine is already up; the boot task
# stopped the same way; and the readiness loop gave up on its first
# `podman info` instead of waiting.
#
# Invoke-Native relaxes the preference for that one call, keeps every line as
# text, and returns the exit code - the only signal a native program gives
# reliably. Each caller decides what a non-zero code means.
#
# ASCII only, on purpose: Windows PowerShell 5.1 reads a .ps1 without a BOM in
# the ANSI code page, and a UTF-8 dash or quote becomes bytes it may parse as
# a string delimiter.

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        # Receives each line as it arrives - for a log file or the console.
        [scriptblock]$OnLine
    )

    # Checked first: with the preference relaxed, a program that is not
    # installed would leave $LASTEXITCODE at whatever the previous one set,
    # and a missing podman would read as success.
    if (-not (Get-Command $FilePath -ErrorAction SilentlyContinue)) {
        throw "Perintah '$FilePath' tidak ditemukan. Pastikan terpasang dan ada di PATH akun ini."
    }

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = @(& $FilePath @ArgumentList 2>&1 | ForEach-Object {
            $line = "$_"
            if ($OnLine) { $null = & $OnLine $line }
            $line
        })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }

    [pscustomobject]@{ ExitCode = $code; Output = $lines }
}

# A podman command that has to succeed. On failure it throws with the last
# lines podman printed, so the reason reaches the log and not just a number.
function Invoke-Podman {
    param(
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$What,
        [scriptblock]$OnLine
    )

    $result = Invoke-Native -FilePath "podman" -ArgumentList $ArgumentList -OnLine $OnLine
    if ($result.ExitCode -ne 0) {
        $tail = ($result.Output | Select-Object -Last 5) -join " | "
        throw "$What gagal (kode $($result.ExitCode)): $tail"
    }
    $result
}

# Starts the machine if it is stopped, then waits until it takes commands.
#
# `machine start` failing is expected: "already running" is its answer on
# every call after the first. Its exit code is therefore ignored, and only
# `podman info` succeeding decides that the machine is ready.
function Wait-PodmanReady {
    param(
        [int]$TimeoutSeconds = 120,
        [scriptblock]$OnLine
    )

    $null = Invoke-Native -FilePath "podman" -ArgumentList @("machine", "start") -OnLine $OnLine

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ($true) {
        if ((Invoke-Native -FilePath "podman" -ArgumentList @("info")).ExitCode -eq 0) { return }
        if ((Get-Date) -ge $deadline) {
            throw "Podman belum siap setelah menunggu $TimeoutSeconds detik. Periksa 'podman machine list' dari akun pemilik machine."
        }
        Start-Sleep -Seconds 2
    }
}
