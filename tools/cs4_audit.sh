#!/bin/bash
# BUOC 4: kiem toan cuoi sau khi ca 3 luong doi sang USERIDALIAS
set +u; . ~/.bash_profile 2>/dev/null || true; set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8
cd /u01/app/ogg || exit 1
PW=$(awk -F= '/^\[ogg_target\]/{f=1} f&&/^password/{gsub(/^[ \t]+|[ \t]+$/,"",$2);print $2;exit}' /u01/app/ggadmin/conf/secrets.ini)

echo "=== cho 150 giay de checkpoint cua ca 3 replicat kip tien len ==="
sleep 150

echo
echo "############ 1. info all ############"
./ggsci <<'GG' 2>/dev/null | grep -E "MANAGER|EXTRACT|REPLICAT"
info all
GG

echo
echo "############ 2. Param file ############"
printf "  mat khau dang ro : %s / 7   (phai 0)\n" "$(grep -ilE '^USERID[[:space:]].*PASSWORD' dirprm/*.prm 2>/dev/null | wc -l)"
printf "  USERIDALIAS      : %s / 7   (phai 6)\n" "$(grep -lE '^USERIDALIAS ' dirprm/*.prm 2>/dev/null | wc -l)"
for f in etpmnv etht etdthu rpmnvods rht rdthu; do
  printf "    %-14s %s\n" "$f.prm" "$(grep -E '^USERIDALIAS ' dirprm/$f.prm)"
done
printf "  TABLE  etpmnv=%s etht=%s etdthu=%s   (phai 52/18/3)\n" \
  "$(grep -c '^TABLE ' dirprm/etpmnv.prm)" "$(grep -c '^TABLE ' dirprm/etht.prm)" "$(grep -c '^TABLE ' dirprm/etdthu.prm)"
printf "  MAP    rpmnvods=%s rht=%s rdthu=%s   (phai 52/18/3)\n" \
  "$(grep -c '^MAP ' dirprm/rpmnvods.prm)" "$(grep -c '^MAP ' dirprm/rht.prm)" "$(grep -c '^MAP ' dirprm/rdthu.prm)"
printf "  HANDLECOLLISIONS tren dong MAP: %s   (phai 0)\n" \
  "$(grep -h '^MAP ' dirprm/rpmnvods.prm dirprm/rht.prm dirprm/rdthu.prm | grep -c HANDLECOLLISIONS)"

echo
echo "############ 3. Credential store ############"
./ggsci <<'GG' 2>&1 | grep -E "Domain|Alias|Userid" | sed 's/^/  /'
info credentialstore
GG
echo "  cwallet.sso: $(stat -c%s dircrd/cwallet.sso) byte, quyen $(stat -c%a dircrd/cwallet.sso)"
echo "  backup     : $(ls -1 /u01/app/ggadmin/backup/cwallet.sso.* 2>/dev/null | wc -l) ban"
echo "  secrets.ini: quyen $(stat -c%a /u01/app/ggadmin/conf/secrets.ini)"

echo
echo "############ 4. Discard + abend ############"
printf "  RPMNVODS.dsc=%s RHT.dsc=%s RDTHU.dsc=%s   (phai 118/113/115)\n" \
  "$(stat -c%s dirrpt/RPMNVODS.dsc)" "$(stat -c%s dirrpt/RHT.dsc)" "$(stat -c%s dirrpt/RDTHU.dsc)"
echo "  abend/fatal hom nay (bo QU_ERROR): $(grep "$(date +%Y-%m-%d)" ggserr.log | grep -E 'ABEND|FATAL' | grep -vi QU_ERROR | wc -l)"
echo "  loi ve credential/login hom nay  : $(grep "$(date +%Y-%m-%d)" ggserr.log | grep -icE 'OGG-00664|ORA-01017|credential|USERIDALIAS.*error' || true)"

echo
echo "############ 5. BANG CHUNG: checkpoint ca 3 replicat PHAI tien len ############"
sqlplus -s OGG_BSH/"$PW"@BSHC <<'SQLEOF' 2>&1 | grep -v "^$"
SET PAGESIZE 0 FEEDBACK OFF LINESIZE 200
PROMPT   moc TRUOC restart:  RHT seqno=5 rba=3896 upd=21:40:01
PROMPT                       RDTHU seqno=2 rba=92743 upd=21:40:03
PROMPT                       RPMNVODS seqno=3 rba=50956720 upd=21:36:33  <- quan trong nhat
PROMPT   --- bay gio ---
SELECT RPAD(group_name,12)||' seqno='||LPAD(seqno,6)||' rba='||LPAD(rba,12)
       ||' log_bsn='||LPAD(NVL(log_bsn,'-'),14)
       ||' upd='||TO_CHAR(last_update_ts,'HH24:MI:SS') FROM ogg_bsh.rht
UNION ALL
SELECT RPAD(group_name,12)||' seqno='||LPAD(seqno,6)||' rba='||LPAD(rba,12)
       ||' log_bsn='||LPAD(NVL(log_bsn,'-'),14)
       ||' upd='||TO_CHAR(last_update_ts,'HH24:MI:SS') FROM ogg_bsh.rdthu
UNION ALL
SELECT RPAD(group_name,12)||' seqno='||LPAD(seqno,6)||' rba='||LPAD(rba,12)
       ||' log_bsn='||LPAD(NVL(log_bsn,'-'),14)
       ||' upd='||TO_CHAR(last_update_ts,'HH24:MI:SS') FROM ogg_bsh.rpmnvods;
PROMPT
PROMPT   capture o nguon - ca 3 phai ENABLED
SELECT RPAD(capture_name,22)||' '||RPAD(status,9)||' req_ckpt='||LPAD(required_checkpoint_scn,12)
       ||' captured='||LPAD(NVL(captured_scn,0),12)
  FROM dba_capture@DBL_PMNV ORDER BY capture_name;
PROMPT
PROMPT   apply o dich - ca 3 phai ENABLED
SELECT RPAD(apply_name,22)||' '||status FROM dba_apply ORDER BY apply_name;
PROMPT
PROMPT   bang prepared o nguon (phai 73) + 3 bang nho
SELECT 'prepared='||(SELECT COUNT(*) FROM dba_capture_prepared_tables@DBL_PMNV)
     ||'  HT_MA_DVI='||(SELECT COUNT(*) FROM ODS.HT_MA_DVI)
     ||'  HT_MA_KENH_TONG='||(SELECT COUNT(*) FROM ODS.HT_MA_KENH_TONG)
     ||'  HT_MA_NGUON='||(SELECT COUNT(*) FROM ODS.HT_MA_NGUON) FROM dual;
EXIT
SQLEOF

echo
echo "############ 6. Backup rollback con nguyen ############"
ls -la dirprm/*.bak_20261013_212925 | awk '{print "  "$1" "$5" "$9}'
