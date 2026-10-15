@echo off
setlocal enabledelayedexpansion
rem ===================================================================
rem  gg-admin.cmd - mo giao dien quan tri GoldenGate
rem
rem  Bam doi vao file nay. No lam ba viec, THEO THU TU:
rem    1. neu duong ham da mo san thi mo browser luon, khong mo ham thu hai
rem    2. mo duong ham SSH  127.0.0.1:8099 -> server 172.16.33.90
rem    3. CHO den khi cong 8099 TRA LOI THAT roi moi mo browser
rem
rem  VI SAO PHAI CHO, KHONG MO BROWSER SAU N GIAY
rem  Ban truoc mo browser sau dung 4 giay. Neu ssh con dang hoi mat khau
rem  hoac mang cham thi browser mo vao mot cong CHUA AI NGHE -> trang trang
rem  tron, va nguoi dung khong biet la do dau. Da xay ra that.
rem
rem  VI SAO DUNG ALIAS `gg-admin` CHU KHONG PHAI oracle@172.16.33.90
rem  Alias nam trong %USERPROFILE%\.ssh\config, no chi dinh khoa rieng
rem  id_ed25519_ggadmin. Khoa do bi gioi han trong authorized_keys tren
rem  server: chi mo duoc DUNG duong ham toi 127.0.0.1:8099, khong chay duoc
rem  lenh nao. Nho vay khong phai go mat khau, ma mat file khoa cung khong
rem  cho ai vao duoc shell oracle.
rem  Chua cai alias thi file nay tu quay ve cach cu (se hoi mat khau).
rem
rem  Cua so nay PHAI GIU MO trong luc dung web. Dong no la dong duong ham.
rem  Viec dai (nap du lieu) chay NGOAI tien trinh web nen dong duong ham
rem  giua luc nap KHONG lam viec do chet.
rem ===================================================================

set PORT=8099
set HOST=oracle@172.16.33.90
set URL=http://127.0.0.1:%PORT%
set PS=powershell -NoProfile -ExecutionPolicy Bypass -Command

echo.
echo  ggadmin - giao dien quan tri Oracle GoldenGate
echo.

rem --- 1. Duong ham da mo san chua? ---------------------------------
rem Viet ro if/else, khong dung `exit (bieu-thuc) -eq $true`: cach do nhap
rem nhang khi PowerShell phan tich, va mot cong kiem ma khong chac chan noi
rem "co" hay "khong" thi vo dung.
%PS% "if (Test-NetConnection -ComputerName 127.0.0.1 -Port %PORT% -InformationLevel Quiet) { exit 1 } else { exit 0 }" >nul 2>&1
if %errorlevel% equ 1 (
  echo  Duong ham da mo san. Dang mo browser...
  start "" %URL%
  echo.
  echo  Khong dong cua so dang giu duong ham do.
  timeout /t 3 >nul
  exit /b 0
)

rem --- 2. Co alias `gg-admin` khong? --------------------------------
set TARGET=gg-admin
ssh -G gg-admin 2>nul | findstr /b /c:"hostname 172.16.33.90" >nul
if errorlevel 1 (
  set TARGET=%HOST%
  echo  [ ! ] Chua cai alias `gg-admin` tren may nay - dung cach cu.
  echo        Se hoi mat khau cua user oracle.
) else (
  echo  Dung alias `gg-admin` + khoa rieng - khong hoi mat khau.
)

rem --- 3. Mo browser SAU KHI cong tra loi that ----------------------
rem Vong cho chay o mot cua so RIENG de khong chan ssh o cua so nay.
start "" /min cmd /c "%PS% \"$n=0; while($n -lt 40){ if(Test-NetConnection -ComputerName 127.0.0.1 -Port %PORT% -InformationLevel Quiet){ Start-Process '%URL%'; exit 0 }; Start-Sleep -Milliseconds 750; $n++ }; exit 1\""

echo.
echo  GIU CUA SO NAY MO trong luc dung web.
echo  Dong cua so = dong duong ham.
echo.

rem -N: khong chay lenh gi, chi forward cong
rem ServerAliveInterval: giu duong ham song khi laptop ngu / mang chap chon
rem ExitOnForwardFailure: khong mo duoc cong thi THOAT, dung im lang chay
rem                       tiep ma khong co duong ham
ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 ^
    -o ExitOnForwardFailure=yes ^
    -L %PORT%:127.0.0.1:%PORT% ^
    %TARGET%

echo.
echo  Duong ham da dong.
echo.
echo  Neu bao "Address already in use": da co mot duong ham khac dang mo,
echo  chi can mo %URL% trong browser.
echo.
echo  Neu bao "Bad owner or permissions on ...\.ssh\config": sua quyen file
echo  bang lenh nay roi chay lai -
echo    icacls "%%USERPROFILE%%\.ssh\config" /inheritance:r /grant:r "%%USERNAME%%:(F)"
echo    icacls "%%USERPROFILE%%\.ssh\id_ed25519_ggadmin" /inheritance:r /grant:r "%%USERNAME%%:(F)"
echo.
pause
