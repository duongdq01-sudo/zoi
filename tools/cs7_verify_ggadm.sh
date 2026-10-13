#!/bin/bash
# =====================================================================
# cs7_verify_ggadm.sh - kiem tra lai 2 account GGADM_* + 2 DB link rieng
#
# Chi doc, khong doi gi. Doc mat khau tu conf/secrets.ini.
# Dung lai duoc nhieu lan, va dung de kiem dinh ky.
#
# Vi sao can file rieng: chay lai cs6_ggadm_users.sh se SINH LAI mat khau
# va ghi de secrets.ini - khong nen lam chi de kiem tra.
# =====================================================================
set -uo pipefail

SEC=/u01/app/ggadmin/conf/secrets.ini
TGT_DSN='localhost:1521/BSHC'
set +u; . ~/.bash_profile 2>/dev/null || true; set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8

hdr() { echo; echo "############ $* ############"; }
getpw() {
  awk -F= -v s="[$1]" '$0==s{f=1} f&&/^password/{gsub(/^[ \t]+|[ \t]+$/,"",$2);print $2;exit}' "$SEC"
}

PW_RO=$(getpw ggadm_ro)
PW_LOAD=$(getpw ggadm_load)
FAILED=0
[ -n "$PW_RO" ]   || { echo "[FAIL] khong doc duoc [ggadm_ro] trong $SEC";   exit 1; }
[ -n "$PW_LOAD" ] || { echo "[FAIL] khong doc duoc [ggadm_load] trong $SEC"; exit 1; }

hdr "0. secrets.ini"
echo "  quyen: $(stat -c%a "$SEC")   cac khoi: $(grep '^\[' "$SEC" | tr '\n' ' ')"
echo "  do dai mat khau: ggadm_ro=${#PW_RO}  ggadm_load=${#PW_LOAD}  (khong in ra)"

hdr "1. Account, profile, quota  (doc bang GGADM_RO, khong can sysdba)"
sqlplus -s -L GGADM_RO/"$PW_RO"@"$TGT_DSN" <<'SQLEOF' 2>&1 | sed 's/^/  /'
SET LINESIZE 200 PAGESIZE 60 FEEDBACK OFF
COL username FOR A12
COL account_status FOR A14
COL profile FOR A14
COL expiry FOR A12
PROMPT === 2 account ===
SELECT username, account_status, profile,
       NVL(TO_CHAR(expiry_date,'YYYY-MM-DD'),'(khong het han)') expiry
  FROM dba_users WHERE username LIKE 'GGADM%' ORDER BY username;
PROMPT
PROMPT === profile GGADMIN_SVC: PASSWORD_LIFE_TIME phai UNLIMITED ===
COL resource_name FOR A26
COL limit FOR A14
SELECT resource_name, limit FROM dba_profiles
 WHERE profile='GGADMIN_SVC' AND resource_name LIKE 'PASSWORD%'
   AND limit <> 'DEFAULT' ORDER BY resource_name;
PROMPT
PROMPT === quota tren BSH_DATA phai la 0 ===
COL username FOR A12
COL tablespace_name FOR A12
SELECT username, tablespace_name, max_bytes FROM dba_ts_quotas
 WHERE username LIKE 'GGADM%' ORDER BY username;
PROMPT
PROMPT === quyen he thong ===
COL grantee FOR A12
COL privilege FOR A24
SELECT grantee, privilege FROM dba_sys_privs
 WHERE grantee LIKE 'GGADM%' ORDER BY grantee, privilege;
PROMPT
PROMPT === role ===
COL granted_role FOR A24
SELECT grantee, granted_role FROM dba_role_privs
 WHERE grantee LIKE 'GGADM%' ORDER BY grantee, granted_role;
PROMPT
PROMPT === so bang ODS duoc cap, theo quyen ===
SELECT grantee, privilege, COUNT(*) so_bang FROM dba_tab_privs
 WHERE grantee LIKE 'GGADM%' AND owner='ODS'
 GROUP BY grantee, privilege ORDER BY grantee, privilege;
PROMPT
PROMPT === DB link ===
COL owner FOR A12
COL db_link FOR A12
COL host FOR A26
SELECT owner, db_link, username, host, TO_CHAR(created,'YYYY-MM-DD HH24:MI') tao
  FROM dba_db_links WHERE owner IN ('OGG_BSH','GGADM_RO','GGADM_LOAD')
 ORDER BY owner;
EXIT
SQLEOF

hdr "2. Link tro dung DB NGUON + flashback query chay duoc"
for a in RO LOAD; do
  if [ "$a" = "RO" ]; then U=GGADM_RO; P="$PW_RO"; else U=GGADM_LOAD; P="$PW_LOAD"; fi
  echo "  --- $U ---"
  OUT=$(sqlplus -s -L "$U"/"$P"@"$TGT_DSN" <<'SQLEOF' 2>&1
SET PAGESIZE 0 FEEDBACK OFF LINESIZE 200
WHENEVER SQLERROR CONTINUE
PROMPT === link tro vao dau ===
SELECT 'nguon: host='||host_name||' instance='||instance_name FROM v$instance@DBL_PMNV;
SELECT 'dich : host='||host_name||' instance='||instance_name FROM v$instance;
PROMPT === doc bang nguon qua link ===
SELECT 'KT_BSH.HT_MA_DVI@nguon = '||COUNT(*) FROM KT_BSH.HT_MA_DVI@DBL_PMNV;
PROMPT === flashback AS OF SCN qua link (bind variable, KHONG subquery) ===
VARIABLE s_scn NUMBER
BEGIN SELECT current_scn - 1 INTO :s_scn FROM v$database@DBL_PMNV; END;
/
SELECT 'AS OF SCN '||:s_scn||' OK, count='||COUNT(*)
  FROM KT_BSH.HT_MA_DVI@DBL_PMNV AS OF SCN :s_scn;
PROMPT === checksum ORA_HASH qua link (cong thuc verify that dung) ===
SELECT 'checksum nguon = '||NVL(SUM(ORA_HASH(NVL(ORA_HASH("MA"),0))),0)
  FROM KT_BSH.HT_MA_DVI@DBL_PMNV AS OF SCN :s_scn;
EXIT
SQLEOF
)
  echo "$OUT" | sed 's/^/    /'
  if echo "$OUT" | grep -q "ip-33-68"; then
    echo "    [ OK ] link tro DUNG vao DB nguon (33-68), khong vong lai dich"
  else
    echo "    [FAIL] link KHONG tro vao 33-68"; FAILED=$((FAILED+1))
  fi
  if echo "$OUT" | grep -q "AS OF SCN .* OK"; then
    echo "    [ OK ] flashback query qua link chay duoc"
  else
    echo "    [FAIL] flashback query qua link KHONG chay"; FAILED=$((FAILED+1))
  fi
  if echo "$OUT" | grep -qE "ORA-[0-9]+"; then
    echo "    [FAIL] co loi ORA:"; echo "$OUT" | grep -oE "ORA-[0-9]+.*" | head -3 | sed 's/^/        /'
    FAILED=$((FAILED+1))
  fi
done

hdr "3. TEST CHAN - quyen phai dung bi gioi han"
echo "  --- GGADM_RO phai KHONG ghi duoc vao ODS ---"
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
  echo "    [ OK ] ORA-01031 - bi chan dung vi thieu quyen"
elif echo "$OUT" | grep -q "ORA-01017"; then
  echo "    [FAIL] ORA-01017 - khong dang nhap duoc, KHONG phai la bi chan"; FAILED=$((FAILED+1))
elif echo "$OUT" | grep -q "GHI_DUOC"; then
  echo "    [FAIL] GGADM_RO GHI DUOC - SAI thiet ke"; FAILED=$((FAILED+1))
else
  echo "    [?] $(echo "$OUT" | grep -E 'ORA-|SP2-' | head -1)"
fi

echo "  --- GGADM_LOAD: ghi duoc dung 73 bang, KHONG phai 128 ---"
sqlplus -s -L GGADM_LOAD/"$PW_LOAD"@"$TGT_DSN" <<'SQLEOF' 2>&1 | sed 's/^/    /'
SET PAGESIZE 0 FEEDBACK OFF LINESIZE 200
SELECT 'ghi duoc (INSERT|DELETE) : '||COUNT(DISTINCT table_name)||' bang  (phai 73)'
  FROM user_tab_privs WHERE owner='ODS' AND privilege IN ('INSERT','DELETE');
SELECT 'doc duoc (SELECT)        : '||COUNT(DISTINCT table_name)||' bang  (phai 73)'
  FROM user_tab_privs WHERE owner='ODS' AND privilege='SELECT';
SELECT 'tong bang trong ODS      : '||COUNT(*)||' bang  (128)'
  FROM dba_tables WHERE owner='ODS';
SELECT 'bang ODS ma GGADM_LOAD KHONG cham duoc : '||
       ((SELECT COUNT(*) FROM dba_tables WHERE owner='ODS')
        - (SELECT COUNT(DISTINCT table_name) FROM user_tab_privs
            WHERE owner='ODS' AND privilege='SELECT'))||'  (phai 55)'
  FROM dual;
EXIT
SQLEOF

echo "  --- GGADM_LOAD phai KHONG TRUNCATE duoc (doi DROP ANY TABLE) ---"
OUT=$(sqlplus -s -L GGADM_LOAD/"$PW_LOAD"@"$TGT_DSN" <<'SQLEOF' 2>&1
WHENEVER SQLERROR CONTINUE
SET FEEDBACK OFF
TRUNCATE TABLE ODS.HT_MA_KENH_TONG;
PROMPT TRUNCATE_DUOC
EXIT
SQLEOF
)
if echo "$OUT" | grep -qE "ORA-01031|ORA-00942"; then
  echo "    [ OK ] bi chan: $(echo "$OUT" | grep -oE 'ORA-[0-9]+' | head -1)"
elif echo "$OUT" | grep -q "TRUNCATE_DUOC"; then
  echo "    [FAIL] TRUNCATE DUOC - SAI, phai bi chan"; FAILED=$((FAILED+1))
else
  echo "    [?] $(echo "$OUT" | grep -E 'ORA-' | head -1)"
fi

hdr "4. Khang dinh KHONG dung den sysdba va khong cham he dang chay"
echo "  Moi truy van tren day chay bang GGADM_RO / GGADM_LOAD, khong sysdba."
echo "  Link cua OGG_BSH (Extract/Replicat dang dung) khong bi cham:"
echo "    $(sqlplus -s -L GGADM_RO/"$PW_RO"@"$TGT_DSN" <<'Q' 2>&1 | tr -d '\n'
SET PAGESIZE 0 FEEDBACK OFF
SELECT 'OGG_BSH.DBL_PMNV tao luc '||TO_CHAR(created,'YYYY-MM-DD HH24:MI')
  FROM dba_db_links WHERE owner='OGG_BSH' AND db_link LIKE 'DBL_PMNV%';
EXIT
Q
)"

hdr "TONG KET"
if [ "$FAILED" -eq 0 ]; then
  echo "  TAT CA OK"
else
  echo "  CO $FAILED MUC FAIL"
fi
exit "$FAILED"
