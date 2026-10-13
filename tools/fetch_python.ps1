<#
  fetch_python.ps1 - tai CPython standalone (relocatable) cho server Linux.

  Vi sao can: server GG chi co Python 3.6.8 (EOL 12/2021) va user `oracle`
  KHONG co sudo nen khong `dnf install` duoc. Ban standalone giai nen vao
  /u01/app/ggadmin/py/ la chay, khong dung gi den Python he thong,
  `rm -rf` la go sach 100%.

  Vi sao chon dung ban nay (da kiem tren server that 2026-10-13):
    - x86_64_v2  : CPU la Xeon Gold 6548N (Emerald Rapids) -> thua dieu kien.
                   v2 chi can SSE4.2/POPCNT/SSSE3/CX16, an toan cho moi server
                   tu ~2009. KHONG lay v3/v4 (can AVX2/AVX512).
    - linux-gnu  : build tren glibc 2.17; server co glibc 2.28 -> thua.
    - install_only: ban dung de chay. KHONG lay ban `full` (archive debug/static,
                   nang hang tram MB, khong can).
    - 3.12 chu KHONG 3.13+: wheel manylinux cua oracledb/pydantic da chin o 3.12.
                   Server khong co gcc va khong xin duoc sudo nen tuyet doi
                   khong duoc de pip phai build tu source.

  Chay tren may Windows co internet. Co verify SHA-256 theo file .sha256
  do chinh upstream phat hanh - khong tin ban tai ve ma khong kiem.
#>

[CmdletBinding()]
param(
  [string]$PyMinor = "3.12",
  [string]$Variant = "x86_64_v2-unknown-linux-gnu-install_only",
  [string]$OutDir  = "vendor-runtime"
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "=== Tim release moi nhat cua astral-sh/python-build-standalone ===" -ForegroundColor Cyan
$rel = Invoke-RestMethod `
  -Uri 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest' `
  -Headers @{ 'User-Agent' = 'ggadmin' }
Write-Host "  tag = $($rel.tag_name)"

$pattern = "cpython-$PyMinor.*-$Variant.tar.gz"
$asset = $rel.assets | Where-Object { $_.name -like $pattern } | Select-Object -First 1
if (-not $asset) { throw "Khong thay asset khop '$pattern' trong release $($rel.tag_name)" }

# Upstream KHONG phat hanh file .sha256 rieng cho tung asset, ma mot file
# SHA256SUMS duy nhat cho ca release (dang "<hash>  <ten file>" moi dong).
$sums = $rel.assets | Where-Object { $_.name -eq 'SHA256SUMS' } | Select-Object -First 1
if (-not $sums) { throw "Release $($rel.tag_name) khong co asset SHA256SUMS - dung lai, khong tai mu." }

Write-Host "=== Lay SHA256SUMS cua release ===" -ForegroundColor Cyan
$sumsTxt = (Invoke-WebRequest -Uri $sums.browser_download_url -Headers @{ 'User-Agent' = 'ggadmin' }).Content
if ($sumsTxt -is [byte[]]) { $sumsTxt = [System.Text.Encoding]::UTF8.GetString($sumsTxt) }

$expect = $null
foreach ($line in ($sumsTxt -split "`n")) {
  $parts = ($line.Trim() -split '\s+')
  if ($parts.Count -ge 2 -and $parts[-1] -eq $asset.name) { $expect = $parts[0].ToLower(); break }
}
if (-not $expect) { throw "Khong tim thay $($asset.name) trong SHA256SUMS" }

$tar = Join-Path $OutDir $asset.name
Write-Host "=== Tai $($asset.name)  ($([math]::Round($asset.size/1MB,1)) MB) ===" -ForegroundColor Cyan
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tar

$local = (Get-FileHash $tar -Algorithm SHA256).Hash.ToLower()
if ($local -ne $expect) {
  Remove-Item $tar -Force
  throw "SHA-256 KHONG KHOP. upstream=$expect local=$local  -> da xoa file tai ve."
}
Write-Host "  SHA-256 KHOP SHA256SUMS: $local" -ForegroundColor Green

# Ghi lai de kiem chung ve sau va de doi chieu sau khi scp sang server
$mf = Join-Path $OutDir "MANIFEST.txt"
@(
  "# CPython standalone cho ggadmin - sinh boi tools/fetch_python.ps1"
  "# $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  "release_tag  $($rel.tag_name)"
  "asset        $($asset.name)"
  "size_bytes   $($asset.size)"
  "sha256       $local"
  "url          $($asset.browser_download_url)"
) | Out-File $mf -Encoding utf8

Write-Host ""
Write-Host "=== Xong ===" -ForegroundColor Green
Write-Host "  $tar"
Write-Host "  $mf"
Write-Host ""
Write-Host "Buoc tiep (xem tools/spike.sh):" -ForegroundColor Cyan
Write-Host "  scp $tar oracle@172.16.33.90:/tmp/"
Write-Host "  ssh ... 'sha256sum /tmp/$($asset.name)'   # phai bang $local"
