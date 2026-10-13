#!/bin/bash
# =====================================================================
# cs6_ggadm_users.sh  - chay tron goi: tao account + DB LINK RIENG + test
#
# CHAY TREN: server 172.16.33.90, user `oracle`
# CACH CHAY:
#     1. Sua 4 bien o muc BIEN duoi day
#     2. chmod 600 cs6_ggadm_users.sh
#     3. bash cs6_ggadm_users.sh --apply
#    (khong co --apply thi chi in ra se lam gi, khong doi gi)
#
# File nay chua MAT KHAU DANG RO -> xoa hoac chmod 600 sau khi chay.
#
# Phan nao chay bang ai:
#   - tao user, grant, profile        -> sqlplus / as sysdba
#   - CREATE DATABASE LINK            -> PHAI bang chinh account do, vi
#                                        link private luon tao trong schema
#                                        cua session hien tai
# =====================================================================
set -uo pipefail

# ---------------------------- BIEN - SUA 4 DONG --------------------------
PW_RO='DoiMatKhauNay_RO_1'
PW_LOAD='DoiMatKhauNay_LOAD_1'
SRC_USER='OGG_BSH'
SRC_PW='DatMatKhauOGG_BSH_O_NGUON'      # mat khau cua OGG_BSH tai DB NGUON
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

hdr "0. Kiem tra truoc"
for v in PW_RO PW_LOAD SRC_PW; do
  case "${!v}" in
    DoiMatKhauNay*|DatMatKhauOGG*) echo "  [FAIL] chua doi bien $v"; exit 1 ;;
  esac
done
echo "  [ OK ] 4 bien da duoc dat"
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

hdr "2. CREATE DATABASE LINK trong GGADM_RO"
sqlplus -s GGADM_RO/"$PW_RO"@"$TGT_DSN" <<SQLEOF 2>&1 | mask
SET FEEDBACK ON LINESIZE 180
WHENEVER SQLERROR CONTINUE
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
SELECT 'AS OF SCN chay duoc, count='||COUNT(*)
  FROM KT_BSH.HT_MA_DVI@DBL_PMNV AS OF SCN (SELECT current_scn-1 FROM v$database@DBL_PMNV);
EXIT
SQLEOF
done

hdr "5. TEST CHAN - quyen phai dung bi gioi han"
echo "  --- GGADM_RO phai KHONG ghi duoc (mong doi ORA-01031) ---"
sqlplus -s GGADM_RO/"$PW_RO"@"$TGT_DSN" <<'SQLEOF' 2>&1 | grep -E "ORA-|da ghi duoc" | sed 's/^/    /'
WHENEVER SQLERROR CONTINUE
SET FEEDBACK OFF
DELETE FROM ODS.HT_MA_DVI WHERE 1=0;
PROMPT !!! da ghi duoc - SAI, phai bi chan
ROLLBACK;
EXIT
SQLEOF
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

hdr "6. KHAI BAO VAO secrets.ini"
cat <<SECEOF
  Them 2 khoi sau vao /u01/app/ggadmin/conf/secrets.ini  (file dang chmod 600):

[ggadm_ro]
user = GGADM_RO
dsn  = BSHC
password = <PW_RO>

[ggadm_load]
user = GGADM_LOAD
dsn  = BSHC
password = <PW_LOAD>

SECEOF

hdr "XONG"
echo "  Viec con lai:"
echo "    - chmod 600 hoac xoa cs6_ggadm_users.sh va cs6_ggadm_users.sql (co mat khau)"
echo "    - de nghi DBA nguon tao account chi-doc GGADM_SRC_RO (muc 9 trong file .sql)"
echo "      roi tao lai 2 link bang credential do. Hien link van dung OGG_BSH@PMNV,"
echo "      ma OGG_BSH o nguon co EXEMPT ACCESS POLICY + EXEMPT REDACTION POLICY."
