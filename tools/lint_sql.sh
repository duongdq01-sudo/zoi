#!/bin/bash
# lint_sql.sh - chan cac loi SQL*Plus da tai pham nhieu lan trong du an nay.
# Chay truoc moi lan scp script len server. Exit != 0 la co loi.
cd "$(dirname "$0")" || exit 1
BAD=0

# Cam bay so 1 cua du an, da tai pham 5 LAN:
# dong PROMPT ket thuc bang '-' -> SQL*Plus coi la ky tu noi dong va NUOT
# cau lenh ke tiep, bao SP2-0734. Luat: dung 'PROMPT === ... ==='.
echo "[1] PROMPT ket thuc bang '-'"
while IFS= read -r l; do
  [ -n "$l" ] && { echo "    $l"; BAD=$((BAD+1)); }
done < <(grep -rn "^[[:space:]]*PROMPT[[:space:]].*-[[:space:]]*$" $(ls ./*.sql ./*.sh 2>/dev/null | grep -v lint_sql.sh) 2>/dev/null)
[ "$BAD" -eq 0 ] && echo "    OK"

# SQL nhung inline vao chuoi ssh '...' bang nhay KEP -> trong Oracle nhay kep
# la identifier chu khong phai chuoi -> ORA-00904. Da mac 2 lan.
echo "[2] AS OF SCN voi subquery (ORA-22818)"
N=$(grep -rn "AS OF SCN[[:space:]]*(" $(ls ./*.sql ./*.sh 2>/dev/null | grep -v lint_sql.sh) 2>/dev/null | grep -ci "SELECT" || true)
if [ "${N:-0}" -gt 0 ]; then
  grep -rn "AS OF SCN[[:space:]]*(" $(ls ./*.sql ./*.sh 2>/dev/null | grep -v lint_sql.sh) 2>/dev/null | grep -i "SELECT" | sed 's/^/    /'
  echo "    -> dung bind variable, khong duoc dung subquery trong AS OF SCN"
  BAD=$((BAD+1))
else
  echo "    OK"
fi

# dba_capture khong co cot capture_message_number o 19c (cam bay 44)
echo "[3] cot khong ton tai: capture_message_number tren dba_capture"
if grep -rln "dba_capture" $(ls ./*.sql ./*.sh 2>/dev/null | grep -v lint_sql.sh) 2>/dev/null | xargs -r grep -l "capture_message_number" 2>/dev/null | grep -q .; then
  echo "    -> dba_capture 19c khong co cot nay; dung required_checkpoint_scn/captured_scn/applied_scn"
  BAD=$((BAD+1))
else
  echo "    OK"
fi

# DATA_DEFAULT_LENGTH khong ton tai, dung DEFAULT_LENGTH
echo "[4] cot khong ton tai: DATA_DEFAULT_LENGTH"
if grep -rli "data_default_length" $(ls ./*.sql ./*.sh 2>/dev/null | grep -v lint_sql.sh) 2>/dev/null | grep -q .; then
  echo "    -> dung DEFAULT_LENGTH cua dba_tab_columns"; BAD=$((BAD+1))
else
  echo "    OK"
fi

# Script chay bang `sqlplus @file` ma THIEU `EXIT` o cuoi -> sqlplus chay
# het script roi CHO INPUT o dau nhac SQL va TREO VINH VIEN. Moi lan chay de
# lai mot session (neu la `/ as sysdba` thi la mot session sysdba treo).
# Da gap that 2026-10-14 voi cs10_ods_index_api.sql: hai session treo.
echo "[5] script .sql thieu EXIT o cuoi"
N5=0
for f in ./*.sql; do
  [ -e "$f" ] || continue
  # Chi xet script dang duoc chay bang sqlplus @file: co dong SET/PROMPT/
  # WHENEVER o dau. Doan SQL de nhung thi khong can EXIT.
  grep -qiE "^(SET |PROMPT |WHENEVER )" "$f" || continue
  if ! tail -5 "$f" | grep -qiE '^(EXIT|QUIT)'; then
    echo "    $f"
    N5=$((N5+1))
  fi
done
if [ "$N5" -gt 0 ]; then
  echo "    -> them EXIT vao cuoi, neu khong sqlplus se treo cho input"
  BAD=$((BAD+N5))
else
  echo "    OK"
fi

echo
if [ "$BAD" -eq 0 ]; then echo "lint: SACH"; else echo "lint: $BAD LOI"; fi
exit "$BAD"
