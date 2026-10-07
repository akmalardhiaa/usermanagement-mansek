[CmdletBinding()]
param(
    [string]$TaskName = "HC Portal Podman startup"
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Jalankan skrip ini dari PowerShell yang dibuka sebagai Administrator."
}

$accountName = Read-Host "Akun Windows pemilik podman machine (default: $($identity.Name))"
if ([string]::IsNullOrWhiteSpace($accountName)) {
    $accountName = $identity.Name
}
$credential = Get-Credential -UserName $accountName -Message "Kredensial Task Scheduler (disimpan oleh Windows Task Scheduler)"

$appRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$scriptPath = Join-Path $appRoot "deploy\windows\start-portal.ps1"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $appRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId $credential.UserName `
    -LogonType Password `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2)

$passwordPointer = [IntPtr]::Zero
try {
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($credential.Password)
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $taskPrincipal `
        -Settings $settings `
        -User $credential.UserName `
        -Password $plainPassword `
        -Force | Out-Null
} finally {
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
    $plainPassword = $null
    $credential = $null
}

Write-Host "Task '$TaskName' didaftarkan saat startup untuk $accountName."
Write-Host "Windows Task Scheduler menyimpan kredensial; skrip tidak menyimpannya."
