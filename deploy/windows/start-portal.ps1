[CmdletBinding()]
param([switch]$Recreate)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'office-runtime.ps1')
Wait-OfficePodman
Start-OfficeStack -Recreate:$Recreate
