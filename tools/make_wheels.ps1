<#
  make_wheels.ps1 - tai san wheel cho server KHONG co internet.

  Chay tren may Windows CO internet. Ket qua: wheelhouse\ chua day du wheel
  cho Python 3.12 / manylinux2014_x86_64, scp sang server roi cai bang:
      py/bin/python3 -m pip install --no-index --find-links=wheelhouse -r requirements.txt

  LUU Y QUAN TRONG:
  pip download tu Windows nham vao Linux se BO QUA dependency co marker
  platform-specific. Vi vay PHAI chay buoc --dry-run tren server truoc khi tin
  rang wheelhouse day du (xem tools/spike.sh buoc 2).
#>

[CmdletBinding()]
param(
  [string]$PyVersion = "3.12",
  [string]$Abi       = "cp312",
  [string]$Platform  = "manylinux2014_x86_64",
  [string]$OutDir    = "wheelhouse"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$req = Join-Path $repo "requirements.txt"
if (-not (Test-Path $req)) { throw "Khong thay requirements.txt tai $req" }

if (Test-Path $OutDir) {
  Write-Host "Xoa $OutDir cu..." -ForegroundColor Yellow
  Remove-Item -Recurse -Force $OutDir
}
New-Item -ItemType Directory -Path $OutDir | Out-Null

Write-Host "=== Tai wheel: Python $PyVersion / $Abi / $Platform ===" -ForegroundColor Cyan

# --only-binary=:all: bat buoc: server khong co gcc va khong xin duoc sudo
# nen moi thu PHAI la wheel da bien dich san, khong duoc build tu source.
python -m pip download `
  --requirement $req `
  --dest $OutDir `
  --platform $Platform `
  --python-version $PyVersion `
  --implementation cp `
  --abi $Abi `
  --only-binary=:all:

if ($LASTEXITCODE -ne 0) { throw "pip download that bai, ma $LASTEXITCODE" }

$files = Get-ChildItem $OutDir -File
$mb = [math]::Round((($files | Measure-Object Length -Sum).Sum / 1MB), 1)

Write-Host ""
Write-Host "=== Xong: $($files.Count) file, $mb MB ===" -ForegroundColor Green
$files | Sort-Object Name | ForEach-Object {
  "{0,10:N0} KB  {1}" -f ($_.Length / 1KB), $_.Name
}

# Wheel 'none-any' la pure Python -> chac chan chay duoc.
# Wheel co ten manylinux -> da bien dich dung cho Linux.
# Neu thay 'win_amd64' thi SAI: da tai wheel cua Windows, dung tren server se loi.
$wrong = $files | Where-Object { $_.Name -match "win_amd64|win32|macosx" }
if ($wrong) {
  Write-Host ""
  Write-Host "!!! CANH BAO: co wheel sai nen tang, server se khong cai duoc:" -ForegroundColor Red
  $wrong | ForEach-Object { Write-Host "    $($_.Name)" -ForegroundColor Red }
  exit 1
}

# Ghi manifest de kiem chung sau nay
$manifest = Join-Path $OutDir "MANIFEST.txt"
"# wheelhouse cho ggadmin - sinh boi tools/make_wheels.ps1"      | Out-File $manifest -Encoding utf8
"# $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  py$PyVersion $Abi $Platform" | Out-File $manifest -Append -Encoding utf8
$files | Sort-Object Name | ForEach-Object {
  $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
  "$h  $($_.Name)" | Out-File $manifest -Append -Encoding utf8
}
Write-Host ""
Write-Host "Da ghi $manifest" -ForegroundColor Green
Write-Host "Buoc tiep: scp -r wheelhouse oracle@172.16.33.90:/u01/app/ggadmin/" -ForegroundColor Cyan
