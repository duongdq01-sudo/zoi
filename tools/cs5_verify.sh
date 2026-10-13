#!/bin/bash
set +u; . ~/.bash_profile 2>/dev/null || true; set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8
cd /u01/app/ogg || exit 1
PW=$(awk -F= '/^\[ogg_target\]/{f=1} f&&/^password/{gsub(/^[ \t]+|[ \t]+$/,"",$2);print $2;exit}' /u01/app/ggadmin/conf/secrets.ini)

echo "############ A. 10 dong log khop mau credential/login - chung la gi ############"
grep "$(date +%Y-%m-%d)" ggserr.log | grep -inE 'OGG-00664|ORA-01017|credential|USERIDALIAS' | sed 's/^/  /'

echo
echo "############ B. Co loi dang nhap THAT nao khong ############"
echo "  ORA-01017 (sai mat khau)      : $(grep -c 'ORA-01017' ggserr.log || true)"
echo "  OGG-00664 (khong dang nhap du): $(grep -c 'OGG-00664' ggserr.log || true)"
echo "  ERROR cua 6 process hom nay   :"
grep "$(date +%Y-%m-%d)" ggserr.log | grep -E '\bERROR\b' | grep -vi QU_ERROR | tail -5 | sed 's/^/    /'
echo "  (trong = sach)"

echo
echo "############ C. Checkpoint ca 3 replicat - lay lai cho du ############"
sqlplus -s OGG_BSH/"$PW"@BSHC <<'SQLEOF' 2>&1 | grep -v "^$"
SET PAGESIZE 0 FEEDBACK OFF LINESIZE 200
PROMPT === checkpoint table 3 replicat ===
SELECT RPAD(group_name,12)||' seqno='||LPAD(seqno,6)||' rba='||LPAD(rba,12)
       ||' log_bsn='||LPAD(NVL(log_bsn,'-'),14)
       ||' upd='||TO_CHAR(last_update_ts,'YYYY-MM-DD HH24:MI:SS')
  FROM ogg_bsh.rht
UNION ALL
SELECT RPAD(group_name,12)||' seqno='||LPAD(seqno,6)||' rba='||LPAD(rba,12)
       ||' log_bsn='||LPAD(NVL(log_bsn,'-'),14)
       ||' upd='||TO_CHAR(last_update_ts,'YYYY-MM-DD HH24:MI:SS')
  FROM ogg_bsh.rdthu
UNION ALL
SELECT RPAD(group_name,12)||' seqno='||LPAD(seqno,6)||' rba='||LPAD(rba,12)
       ||' log_bsn='||LPAD(NVL(log_bsn,'-'),14)
       ||' upd='||TO_CHAR(last_update_ts,'YYYY-MM-DD HH24:MI:SS')
  FROM ogg_bsh.rpmnvods;
EXIT
SQLEOF

echo
echo "############ D. Low/High Watermark ca 3 replicat (dblogin bang ALIAS) ############"
./ggsci <<'GG' 2>/dev/null | grep -viE "post-quantum|store now|upgraded" | grep -E "^Replicat|Watermark CSN"
dblogin useridalias bshc_ogg
info replicat RHT, detail
info replicat RDTHU, detail
info replicat RPMNVODS, detail
GG
