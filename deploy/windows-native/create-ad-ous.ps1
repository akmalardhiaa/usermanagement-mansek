# Jalankan dari member server dengan RSAT ActiveDirectory dan akun yang didelegasikan.
# Secara default hanya menampilkan rencana. -Apply membuat OU yang belum ada.
param(
    [Parameter(Mandatory = $true)][string]$BaseDn,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop
$domain = Get-ADDomain -ErrorAction Stop
if ($BaseDn -ine $domain.DistinguishedName) {
    throw "BaseDn harus sama dengan DN domain yang terhubung: $($domain.DistinguishedName)"
}

function Ensure-Ou([string]$Name, [string]$ParentDn) {
    $dn = "OU=$Name,$ParentDn"
    if (-not $Apply -and $ParentDn -ine $BaseDn -and
        -not (Get-ADOrganizationalUnit -Identity $ParentDn -ErrorAction SilentlyContinue)) {
        Write-Host "Akan dibuat: $dn"
        return
    }
    $existing = Get-ADOrganizationalUnit -Filter "Name -eq '$Name'" -SearchBase $ParentDn -SearchScope OneLevel -ErrorAction Stop |
        Where-Object { $_.DistinguishedName -ieq $dn }
    if ($existing) {
        Write-Host "Sudah ada: $dn"
        return
    }
    if (-not $Apply) {
        Write-Host "Akan dibuat: $dn"
        return
    }
    New-ADOrganizationalUnit -Name $Name -Path $ParentDn -ProtectedFromAccidentalDeletion $true -ErrorAction Stop
    Write-Host "Dibuat: $dn"
}

$hq = "OU=Kantor Pusat,$BaseDn"
$branch = "OU=Kantor Cabang,$BaseDn"
$external = "OU=External,$BaseDn"
$externalBranch = "OU=Cabang,$external"
foreach ($name in @('Kantor Pusat', 'Kantor Cabang', 'External', 'Intern', 'Karantina')) {
    Ensure-Ou $name $BaseDn
}
foreach ($name in @('Capital Market', 'CEO Functional', 'Generic', 'Intren', 'Invesment Banking',
                    'Operations', 'Retail', 'Settelment and Custody', 'Tecnology')) {
    Ensure-Ou $name $hq
}
$cities = @('Bandung', 'Batam', 'Jakarta', 'Makasar', 'Medan', 'Semarang', 'Singapore Subsidiary', 'Surabaya')
foreach ($name in $cities) { Ensure-Ou $name $branch }
Ensure-Ou 'Pusat' $external
Ensure-Ou 'Cabang' $external
foreach ($name in $cities) { Ensure-Ou $name $externalBranch }

if (-not $Apply) { Write-Host 'Pratinjau selesai. Jalankan lagi dengan -Apply setelah DN dan nama OU diverifikasi.' }
