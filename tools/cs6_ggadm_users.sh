#!/bin/bash
# =====================================================================
# cs6_ggadm_users.sh  - chay tron goi: tao account + DB LINK RIENG + test
#
# CHAY TREN: server 172.16.33.90, user `oracle`
# CACH CHAY:
#     bash cs6_ggadm_users.sh            # dry-run, khong doi gi
#     bash cs6_ggadm_users.sh --apply    # lam that
#    (khong co --apply thi chi in ra se lam gi, khong doi gi)
#
# File nay KHONG chua mat khau: mat khau 2 account moi duoc TU SINH va ghi
# thang vao conf/secrets.ini (0600); mat khau nguon doc tu file do.
#
# Phan nao chay bang ai:
#   - tao user, grant, profile        -> sqlplus / as sysdba
#   - CREATE DATABASE LINK            -> PHAI bang chinh account do, vi
#                                        link private luon tao trong schema
#                                        cua session hien tai
# =====================================================================
set -uo pipefail

# ---------------------------- BIEN --------------------------------------
# Khong phai go mat khau o dau. Mac dinh:
#   - mat khau 2 account moi : TU SINH 24 ky tu, ghi THANG vao secrets.ini
#   - mat khau nguon         : DOC tu secrets.ini [ogg_source]
# Muon tu dat thi truyen bien moi truong GGADM_PW_RO / GGADM_PW_LOAD /
# GGADM_SRC_PW / GGADM_SRC_USER.
PW_RO="${GGADM_PW_RO:-}"
PW_LOAD="${GGADM_PW_LOAD:-}"
SRC_USER="${GGADM_SRC_USER:-OGG_BSH}"
SRC_PW="${GGADM_SRC_PW:-}"
# ------------------------------------------------------------------------

SRC_DSN='PMNV'                          # alias TNS trong tnsnames.ora cua DB server
TGT_DSN='localhost:1521/BSHC'
SQLFILE="$(dirname "$0")/cs6_ggadm_users.sql"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

set +u; . ~/.bash_profile 2>/dev/null || true; set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8

hdr() { echo; echo "############ $* ############"; }
# Chan mat khau lot ra output trong moi truong hop
mask() { sed -e "s/$PW_RO/***/g" -e "s/$PW_LOAD/***/g" -e "s/$SRC_PW/***/g"; }

FAILED=0

hdr "0. Kiem tra truoc"
SEC=/u01/app/ggadmin/conf/secrets.ini
[ -f "$SEC" ] || { echo "  [FAIL] khong thay $SEC"; exit 1; }
echo "  [ OK ] $SEC (quyen $(stat -c%a "$SEC"))"

# Mat khau NGUON: lay tu secrets.ini, khong phai go lai
if [ -z "$SRC_PW" ]; then
  SRC_PW=$(awk -F= '/^\[ogg_source\]/{f=1} f&&/^password/{gsub(/^[ \t]+|[ \t]+$/,"",$2);print $2;exit}' "$SEC")
  if [ -n "$SRC_PW" ]; then
    echo "  [ OK ] mat khau nguon lay tu secrets.ini [ogg_source] (khong in ra)"
  else
    echo "  [FAIL] khong doc duoc mat khau nguon tu secrets.ini"; exit 1
  fi
else
  echo "  [ OK ] mat khau nguon lay tu bien moi truong"
fi

# Mat khau 2 account moi: tu sinh 24 ky tu, bat dau bang chu cai.
# Chi [A-Za-z0-9_] de khong vuong quoting cua shell lan SQL*Plus.
genpw() { echo "G$(LC_ALL=C tr -dc 'A-Za-z0-9_' < /dev/urandom | head -c23)"; }
if [ -z "$PW_RO" ]; then
  PW_RO=$(genpw); echo "  [ OK ] da sinh mat khau GGADM_RO   (${#PW_RO} ky tu, khong in ra)"
else
  echo "  [ OK ] mat khau GGADM_RO lay tu bien moi truong"
fi
if [ -z "$PW_LOAD" ]; then
  PW_LOAD=$(genpw); echo "  [ OK ] da sinh mat khau GGADM_LOAD (${#PW_LOAD} ky tu, khong in ra)"
else
  echo "  [ OK ] mat khau GGADM_LOAD lay tu bien moi truong"
fi
[ -f "$SQLFILE" ] || { echo "  [FAIL] khong thay $SQLFILE"; exit 1; }
echo "  [ OK ] $SQLFILE"

# Link dung alias TNS nen PHAI chac alias phan giai dung TRUOC khi tao link.
# Ca 2 DB cung SERVICE_NAME=BSHC, phan biet nam trong tnsnames.ora -> neu
# alias PMNV bi sua tro vao DICH thi link se vong lai chinh minh.
TNSDIR="${TNS_ADMIN:-$ORACLE_HOME/network/admin}"
echo "  tnsnames : $TNSDIR/tnsnames.ora"
if grep -qiE "^[[:space:]]*$SRC_DSN[[:space:]]*=" "$TNSDIR/tnsnames.ora" 2>/dev/null; then
  echo "  [ OK ] co entry $SRC_DSN trong tnsnames.ora"
  echo "         HOST   = $(awk "/^[[:space:]]*$SRC_DSN[[:space:]]*=/,/^[[:space:]]*\\$/" "$TNSDIR/tnsnames.ora" | grep -oiE "HOST[[:space:]]*=[[:space:]]*[^)[:space:]]+" | head -1)"
  echo "         SERVICE= $(awk "/^[[:space:]]*$SRC_DSN[[:space:]]*=/,/^[[:space:]]*\\$/" "$TNSDIR/tnsnames.ora" | grep -oiE "SERVICE_NAME[[:space:]]*=[[:space:]]*[^)[:space:]]+" | head -1)"
else
  echo "  [FAIL] khong thay entry $SRC_DSN trong $TNSDIR/tnsnames.ora"; exit 1
fi
if command -v tnsping >/dev/null 2>&1; then
  if tnsping "$SRC_DSN" 2>&1 | grep -q "OK ("; then
    echo "  [ OK ] tnsping $SRC_DSN thanh cong"
  else
    echo "  [FAIL] tnsping $SRC_DSN that bai:"
    tnsping "$SRC_DSN" 2>&1 | tail -3 | sed "s/^/        /"; exit 1
  fi
fi
echo "  nguon : $SRC_USER@$SRC_DSN"
echo "  dich  : $TGT_DSN"
if [ "$APPLY" -eq 0 ]; then
  echo
  echo "  CHE DO DRY-RUN. Se lam:"
  echo "    1. sqlplus / as sysdba @cs6_ggadm_users.sql  (tao profile, 2 user, grant)"
  echo "    2. CREATE DATABASE LINK DBL_PMNV trong GGADM_RO"
  echo "    3. CREATE DATABASE LINK DBL_PMNV trong GGADM_LOAD"
  echo "    4. Test that: doc v\$database va dem 1 bang qua tung link"
  echo "    5. Test chan: GGADM_RO phai KHONG ghi duoc vao ODS"
  echo
  echo "  Chay lai voi --apply de thuc hien."
  exit 0
fi

hdr "1. Tao profile + 2 account + grant  (sysdba)"
# Truyen mat khau qua bien SQL*Plus de khong nam trong file .sql
sqlplus -s / as sysdba <<SQLEOF 2>&1 | mask
DEFINE pw_ro   = '$PW_RO'
DEFINE pw_load = '$PW_LOAD'
DEFINE src_user = '$SRC_USER'
DEFINE src_pw   = '$SRC_PW'
DEFINE src_dsn  = '$SRC_DSN'
@$SQLFILE
SQLEOF

hdr "1b. CONG KIEM: 2 account phai dang nhap duoc"
# Loi da gap that: file .sql tu DEFINE lai mat khau -> khong account nao
# duoc tao, nhung script van chay tiep va ghi mat khau vao secrets.ini.
# Cong kiem nay chan dung kich ban do.
for a in RO LOAD; do
  if [ "$a" = "RO" ]; then U=GGADM_RO; P="$PW_RO"; else U=GGADM_LOAD; P="$PW_LOAD"; fi
  OUT=$(sqlplus -s -L "$U"/"$P"@"$TGT_DSN" <<'QEOF' 2>&1
SET PAGESIZE 0 FEEDBACK OFF
SELECT 'LOGIN_OK ' || USER FROM dual;
EXIT
QEOF
)
  if echo "$OUT" | grep -q "LOGIN_OK"; then
    echo "  [ OK ] $U dang nhap duoc: $(echo "$OUT" | grep LOGIN_OK)"
  else
    echo "  [FAIL] $U KHONG dang nhap duoc:"
    echo "$OUT" | grep -E "ORA-|SP2-" | head -3 | mask | sed 's/^/        /'
    FAILED=$((FAILED+1))
  fi
done
if [ "$FAILED" -ne 0 ]; then
  echo
  echo "  !!! DUNG LAI. Khong tao link, KHONG ghi gi vao secrets.ini."
  echo "      secrets.ini giu nguyen nhu truoc khi chay script nay."
  exit 1
fi

hdr "2. CREATE DATABASE LINK trong GGADM_RO"
sqlplus -s GGADM_RO/"$PW_RO"@"$TGT_DSN" <<SQLEOF 2>&1 | mask
SET FEEDBACK ON LINESIZE 180
WHENEVER SQLERROR CONTINUE
-- Drop truoc de chay lai duoc va de doi credential nguon ve sau.
-- Link private nen chi anh huong chinh schema nay, khong cham link cua
-- OGG_BSH dang duoc Extract/Replicat su dung.
DROP DATABASE LINK DBL_PMNV;
-- Dat cung ten DBL_PMNV nhu link cua OGG_BSH, nho vay moi cau
-- ... @DBL_PMNV trong tai lieu va script cu chay duoc y nguyen.
CREATE DATABASE LINK DBL_PMNV
  CONNECT TO $SRC_USER IDENTIFIED BY "$SRC_PW"
  USING '$SRC_DSN';
EXIT
SQLEOF

hdr "3. CREATE DATABASE LINK trong GGADM_LOAD"
sqlplus -s GGADM_LOAD/"$PW_LOAD"@"$TGT_DSN" <<SQLEOF 2>&1 | mask
SET FEEDBACK ON LINESIZE 180
WHENEVER SQLERROR CONTINUE
DROP DATABASE LINK DBL_PMNV;
CREATE DATABASE LINK DBL_PMNV
  CONNECT TO $SRC_USER IDENTIFIED BY "$SRC_PW"
  USING '$SRC_DSN';
EXIT
SQLEOF

hdr "4. TEST THAT - link phai doc duoc nguon, va dung DB nguon"
for acct in RO LOAD; do
  if [ "$acct" = "RO" ]; then U=GGADM_RO; P="$PW_RO"; else U=GGADM_LOAD; P="$PW_LOAD"; fi
  echo "  --- $U ---"
  sqlplus -s "$U"/"$P"@"$TGT_DSN" <<'SQLEOF' 2>&1 | sed 's/^/    /'
SET PAGESIZE 0 FEEDBACK OFF LINESIZE 200
WHENEVER SQLERROR CONTINUE
PROMPT === doc duoc DB nguon qua link chua ===
SELECT 'nguon: db='||name||' log_mode='||log_mode||' scn='||current_scn
  FROM v$database@DBL_PMNV;
PROMPT === dung DB NGUON chu khong phai vong lai chinh minh ===
PROMPT     (ca 2 DB cung SERVICE_NAME=BSHC nen phai so instance/host)
SELECT 'nguon host='||host_name||'  instance='||instance_name
  FROM v$instance@DBL_PMNV;
SELECT 'dich  host='||host_name||'  instance='||instance_name
  FROM v$instance;
PROMPT === doc duoc bang KT_BSH qua link chua ===
SELECT 'KT_BSH.HT_MA_DVI@nguon = '||COUNT(*) FROM KT_BSH.HT_MA_DVI@DBL_PMNV;
PROMPT === doc duoc bang ODS o dich chua ===
SELECT 'ODS.HT_MA_DVI@dich = '||COUNT(*) FROM ODS.HT_MA_DVI;
PROMPT === flashback query qua link co chay khong (verify can cai nay) ===
-- ORA-22818: AS OF SCN KHONG nhan subquery. Phai lay SCN ra bind variable
-- truoc. Day la loi da gap that 2026-10-13.
VARIABLE s_scn NUMBER
BEGIN SELECT current_scn - 1 INTO :s_scn FROM v$database@DBL_PMNV; END;
/
SELECT 'AS OF SCN '||:s_scn||' chay duoc, count='||COUNT(*)
  FROM KT_BSH.HT_MA_DVI@DBL_PMNV AS OF SCN :s_scn;
EXIT
SQLEOF
done

hdr "5. TEST CHAN - quyen phai dung bi gioi han"
echo "  --- GGADM_RO phai KHONG ghi duoc ---"
# PHAN BIET RO: ORA-01031 = dung (thieu quyen, da bi chan).
# ORA-01017 = KHONG dang nhap duoc, day la LOI chu khong phai thanh cong.
OUT=$(sqlplus -s -L GGADM_RO/"$PW_RO"@"$TGT_DSN" <<'SQLEOF' 2>&1
WHENEVER SQLERROR CONTINUE
SET FEEDBACK OFF
DELETE FROM ODS.HT_MA_DVI WHERE 1=0;
PROMPT GHI_DUOC
ROLLBACK;
EXIT
SQLEOF
)
if echo "$OUT" | grep -q "ORA-01031"; then
  echo "    [ OK ] bi chan dung bang ORA-01031 (thieu quyen)"
elif echo "$OUT" | grep -q "ORA-00942"; then
  echo "    [ OK ] bi chan bang ORA-00942 (khong thay bang) - cung la chan"
elif echo "$OUT" | grep -q "ORA-01017"; then
  echo "    [FAIL] ORA-01017 - khong dang nhap duoc, KHONG phai la bi chan"
  FAILED=$((FAILED+1))
elif echo "$OUT" | grep -q "GHI_DUOC"; then
  echo "    [FAIL] GGADM_RO GHI DUOC vao ODS - SAI thiet ke"
  FAILED=$((FAILED+1))
else
  echo "    [?] khong ro ket qua:"; echo "$OUT" | grep -E "ORA-|SP2-" | head -3 | sed 's/^/        /'
fi
echo "  --- GGADM_LOAD phai KHONG ghi duoc vao bang NGOAI 73 bang ---"
sqlplus -s GGADM_LOAD/"$PW_LOAD"@"$TGT_DSN" <<'SQLEOF' 2>&1 | sed 's/^/    /'
SET PAGESIZE 0 FEEDBACK OFF LINESIZE 200
SELECT 'so bang ODS ma GGADM_LOAD ghi duoc = '||COUNT(DISTINCT table_name)||' (mong doi 73)'
  FROM user_tab_privs WHERE owner='ODS' AND privilege IN ('INSERT','DELETE');
SELECT 'so bang ODS ma GGADM_LOAD doc duoc = '||COUNT(DISTINCT table_name)||' (mong doi 73)'
  FROM user_tab_privs WHERE owner='ODS' AND privilege='SELECT';
EXIT
SQLEOF
echo "  --- GGADM_LOAD phai KHONG TRUNCATE duoc (mong doi ORA-01031) ---"
echo "      TRUNCATE doi DROP ANY TABLE, co y khong cap."
echo "      Wizard re-sync se TRUNCATE bang ket noi ODS, giong REBUILD index."

hdr "6. Ghi 2 khoi vao secrets.ini"
if [ "$FAILED" -ne 0 ]; then
  echo "  !!! CO $FAILED MUC FAIL -> KHONG ghi secrets.ini."
  echo "      Mat khau vua sinh bi bo di. secrets.ini giu nguyen."
  echo "      Sua loi roi chay lai; script idempotent nen chay lai an toan."
  exit 1
fi
cp -p "$SEC" "$SEC.bak_$(date +%Y%m%d_%H%M%S)"
chmod 600 "$SEC".bak_* 2>/dev/null || true
echo "  da backup -> $SEC.bak_*"
# Xoa khoi cu truoc khi ghi -> chay lai nhieu lan khong trung lap
GGADM_SEC="$SEC" GGADM_A="$PW_RO" GGADM_B="$PW_LOAD" python3 - <<'PYSEC'
import io, os, re
path = os.environ["GGADM_SEC"]
s = io.open(path, encoding="utf-8").read()
for name in ("ggadm_ro", "ggadm_load"):
    s = re.sub(r"\n\[" + name + r"\][^\[]*", "\n", s)
s = s.rstrip() + (
    "\n\n[ggadm_ro]\nuser = GGADM_RO\ndsn  = BSHC\npassword = %s\n"
    "\n[ggadm_load]\nuser = GGADM_LOAD\ndsn  = BSHC\npassword = %s\n"
    % (os.environ["GGADM_A"], os.environ["GGADM_B"])
)
io.open(path, "w", encoding="utf-8", newline="\n").write(s)
print("  da ghi [ggadm_ro] va [ggadm_load]")
PYSEC
chmod 600 "$SEC"
echo "  quyen secrets.ini : $(stat -c%a "$SEC")"
echo "  cac khoi hien co  : $(grep -c '^\[' "$SEC") -> $(grep '^\[' "$SEC" | tr '\n' ' ')"
echo "  (mat khau KHONG in ra o dau; doc bang awk nhu cac script khac)"

hdr "TONG KET"
if [ "$FAILED" -eq 0 ]; then
  echo "  TAT CA OK - 2 account + 2 link rieng da san sang."
else
  echo "  CO $FAILED MUC FAIL."
fi

hdr "XONG"
echo "  Viec con lai:"
echo "    - 2 file cs6_* KHONG con chua mat khau nao (tu sinh + doc tu secrets.ini)"
echo "    - de nghi DBA nguon tao account chi-doc GGADM_SRC_RO (muc 9 trong file .sql)"
echo "      roi tao lai 2 link bang credential do. Hien link van dung OGG_BSH@PMNV,"
echo "      ma OGG_BSH o nguon co EXEMPT ACCESS POLICY + EXEMPT REDACTION POLICY."
