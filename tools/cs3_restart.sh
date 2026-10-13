#!/bin/bash
# =====================================================================
# cs3_restart.sh <HT|DT|PM> [--apply]   - BUOC 3/4: restart cuon
#
# MAC DINH DRY-RUN. Chi restart khi truyen --apply.
# Moi lan chi lam MOT LUONG, de co diem dung ra soat giua cac luong.
# Thu tu khuyen nghi: HT (nhe nhat) -> DT -> PM (nang nhat).
#
# VI SAO KHONG MAT DU LIEU:
#   Lan nay danh sach TABLE/MAP KHONG doi mot dong nao - chi doi cach lay
#   credential. Nen KHONG co rui ro "Replicat bo qua record khong khop MAP
#   ma van doi checkpoint" nhu khi them bang. Process restart tiep tuc tu
#   checkpoint cua chinh no:
#     - Extract : dirchk/<ET>.cpe + required_checkpoint_scn cua capture
#     - Replicat: inbound server OGG$<R> + checkpoint table OGG_BSH.<R>
#
# THU TU TRONG MOT LUONG: Replicat TRUOC, Extract SAU.
#   Neu alias sai thi Replicat khong start duoc la truong hop LANH hon:
#   trail van giu du lieu tren dia. Extract khong start duoc moi dang lo
#   vi redo o nguon cu tich luy.
#
# CONG CUNG: process nao khong tro lai RUNNING + lag ve nguong thi DUNG
# TOAN BO, khong sang process tiep theo.
# =====================================================================
set -uo pipefail

GGHOME=/u01/app/ogg
GGADMIN=/u01/app/ggadmin
SEC="$GGADMIN/conf/secrets.ini"
STREAM="${1:-}"
APPLY=0
[ "${2:-}" = "--apply" ] && APPLY=1
LAG_MAX=90          # giay: nguong lag coi la da bat kip
WAIT_STOP=40        # so lan kiem x 3s khi cho STOPPED
WAIT_START=60       # so lan kiem x 5s khi cho RUNNING + lag

case "$STREAM" in
  HT) EXT=ETHT;   REP=RHT;      PRM_E=etht.prm;   PRM_R=rht.prm;      TRAIL=ht; DSC=113 ;;
  DT) EXT=ETDTHU; REP=RDTHU;    PRM_E=etdthu.prm; PRM_R=rdthu.prm;    TRAIL=dt; DSC=115 ;;
  PM) EXT=ETPMNV; REP=RPMNVODS; PRM_E=etpmnv.prm; PRM_R=rpmnvods.prm; TRAIL=pm; DSC=118 ;;
  *)  echo "Dung: cs3_restart.sh <HT|DT|PM> [--apply]"; exit 1 ;;
esac

set +u; . ~/.bash_profile 2>/dev/null || true; set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8
cd "$GGHOME" || exit 1

PW=$(awk -F= '/^\[ogg_target\]/{f=1} f&&/^password/{gsub(/^[ \t]+|[ \t]+$/,"",$2);print $2;exit}' "$SEC")
[ -n "$PW" ] || { echo "[FAIL] khong doc duoc mat khau tu $SEC"; exit 1; }

hdr()   { echo; echo "############ $* ############"; }
sub()   { echo; echo "--- $* ---"; }
noise() { grep -viE "post-quantum|store now|upgraded"; }
indent(){ sed -e "s/^/    /"; }
maskpw(){ sed -E "s/(PASSWORD[[:space:]]+)[^ ,]+/\1***/Ig"; }

# lag hh:mm:ss -> giay
lag2s() { echo "$1" | awk -F: "{if(NF==3) print \$1*3600+\$2*60+\$3; else print 99999}"; }

# Trang thai + lag cua 1 process, doc tu 'info all'.
# Cam bay 26: KHONG doc lag tu 'info ... detail' vi dong "Last Started"
# lam cot 4 thanh chu 'Started', so sanh voi 00:00:00 se khong bao gio bang.
pstate() {
  ./ggsci <<'GGEOF' 2>/dev/null | awk -v p="$1" '$3==p {print $2" "$4}'
info all
GGEOF
}

# Chi tiet seqno/RBA/checkpoint. dblogin bang ALIAS - khong con mat khau.
pdetail() {
  ./ggsci <<GGEOF 2>/dev/null | noise | grep -E "Log Read Checkpoint|Log BSN|Watermark|Status |Checkpoint Lag|dirdat/|Trail Name|SCN"
dblogin useridalias bshc_ogg
info $1, detail
GGEOF
}

# Bang chung tu DB: tien do capture (nguon) + apply (dich) + so dong 3 bang nho
evidence() {
  sqlplus -s OGG_BSH/"$PW"@BSHC <<'SQLEOF' 2>&1 | grep -v "^$"
SET PAGESIZE 0 FEEDBACK OFF LINESIZE 200 TRIMSPOOL ON
PROMPT === capture o NGUON. req_ckpt KHONG duoc nhay vuot sau restart ===
SELECT RPAD(capture_name,22)||' '||RPAD(status,9)
       ||' req_ckpt='||LPAD(required_checkpoint_scn,12)
       ||' captured='||LPAD(NVL(captured_scn,0),12)
       ||' applied='||LPAD(NVL(applied_scn,0),12)
       ||' last_enq='||LPAD(NVL(last_enqueued_scn,0),12)
  FROM dba_capture@DBL_PMNV ORDER BY capture_name;
PROMPT === apply o DICH (chi can ENABLED, GG khong dung applied_message_number) ===
SELECT RPAD(apply_name,22)||' '||status FROM dba_apply ORDER BY apply_name;
PROMPT === checkpoint table 3 replicat. seqno/rba phai tien len, KHONG duoc ve 0 ===
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
PROMPT === so dong 3 bang nho (chi duoc TANG, khong duoc giam) ===
SELECT 'HT_MA_DVI='||(SELECT COUNT(*) FROM ODS.HT_MA_DVI)
     ||' HT_MA_KENH_TONG='||(SELECT COUNT(*) FROM ODS.HT_MA_KENH_TONG)
     ||' HT_MA_NGUON='||(SELECT COUNT(*) FROM ODS.HT_MA_NGUON)
     ||' scn_nguon='||(SELECT current_scn FROM v$database@DBL_PMNV) FROM dual;
EXIT
SQLEOF
}

trailinfo() {
  local f
  f=$(ls -1 dirdat/${TRAIL}* 2>/dev/null | sort | tail -1)
  [ -n "$f" ] && echo "  trail moi nhat: $f  $(stat -c%s "$f") byte"
}

# ---------------------------------------------------------------------
hdr "LUONG $STREAM:  $EXT -> trail $TRAIL -> $REP"
if [ "$APPLY" -eq 1 ]; then
  echo "  CHE DO: --apply  (SE RESTART THAT)"
else
  echo "  CHE DO: DRY-RUN  (chi in bang chung, khong restart)"
fi

hdr "A. TIEN DIEU KIEN"
PLAIN=$(grep -ilE '^USERID[[:space:]].*PASSWORD' dirprm/*.prm 2>/dev/null | wc -l)
NALIAS=$(grep -lE '^USERIDALIAS ' dirprm/*.prm 2>/dev/null | wc -l)
echo "  param: mat khau dang ro=$PLAIN (phai 0)   USERIDALIAS=$NALIAS (phai 6)"
if [ "$PLAIN" -ne 0 ] || [ "$NALIAS" -ne 6 ]; then
  echo "  [FAIL] param chua dung -> chay cs2_prm.sh --apply truoc"; exit 1
fi
echo "  alias trong $PRM_E: $(grep -E '^USERIDALIAS ' dirprm/$PRM_E)"
echo "  alias trong $PRM_R: $(grep -E '^USERIDALIAS ' dirprm/$PRM_R)"
echo "  dircrd/cwallet.sso: $(stat -c%s dircrd/cwallet.sso 2>/dev/null) byte"
sub "dirprm/mgr.prm (xem co AUTORESTART khong)"
grep -ivE '^--|^$' dirprm/mgr.prm | maskpw | indent
sub "info all TRUOC"
./ggsci <<'GGEOF' 2>/dev/null | grep -E "MANAGER|EXTRACT|REPLICAT"
info all
GGEOF
echo "  discard: RPMNVODS=$(stat -c%s dirrpt/RPMNVODS.dsc) RHT=$(stat -c%s dirrpt/RHT.dsc) RDTHU=$(stat -c%s dirrpt/RDTHU.dsc)"
echo "  abend/fatal hom nay (bo QU_ERROR): $(grep "$(date +%Y-%m-%d)" ggserr.log 2>/dev/null | grep -E "ABEND|FATAL" | grep -vi QU_ERROR | wc -l)"

hdr "B. BANG CHUNG TRUOC KHI RESTART"
trailinfo
sub "$REP detail"; pdetail "$REP" | indent
sub "$EXT detail"; pdetail "$EXT" | indent
sub "DB evidence";  evidence | indent

if [ "$APPLY" -eq 0 ]; then
  hdr "DRY-RUN KET THUC - chua restart gi"
  echo "  Chay lai: cs3_restart.sh $STREAM --apply"
  exit 0
fi

# ---------------------------------------------------------------------
restart_one() {
  local P="$1" KIND="$2"
  hdr "RESTART $KIND $P"
  echo "  truoc: $(pstate "$P")"

  ./ggsci <<GGEOF 2>&1 | noise | grep -iE "Request|Sending|error" | indent
stop $KIND $P
GGEOF

  local i st lag lsec
  for i in $(seq 1 $WAIT_STOP); do
    st=$(pstate "$P" | awk '{print $1}')
    if [ "$st" = "STOPPED" ]; then echo "    STOPPED sau ~$((i*3)) giay"; break; fi
    sleep 3
  done
  st=$(pstate "$P" | awk '{print $1}')
  if [ "$st" != "STOPPED" ]; then
    echo "    [FAIL] $P khong ve STOPPED (dang '$st') -> DUNG TOAN BO"
    return 1
  fi

  ./ggsci <<GGEOF 2>&1 | noise | grep -iE "Request|Sending|starting|error" | indent
start $KIND $P
GGEOF

  for i in $(seq 1 $WAIT_START); do
    sleep 5
    st=$(pstate "$P" | awk '{print $1}')
    lag=$(pstate "$P" | awk '{print $2}')
    lag=${lag:-99:99:99}
    lsec=$(lag2s "$lag")
    printf "    %s  %-9s lag=%s\n" "$(date +%H:%M:%S)" "${st:-?}" "$lag"
    if [ "$st" = "ABENDED" ]; then
      echo "    [FAIL] $P ABENDED -> DUNG TOAN BO"
      grep "$(date +%Y-%m-%d)" ggserr.log | grep -i "$P" | tail -6 | indent
      return 1
    fi
    if [ "$st" = "RUNNING" ] && [ "$lsec" -le "$LAG_MAX" ] 2>/dev/null; then
      echo "    [ OK ] $P RUNNING, lag $lag (<= ${LAG_MAX}s) sau ~$((i*5)) giay"
      return 0
    fi
  done
  echo "    [FAIL] $P khong bat kip trong $((WAIT_START*5)) giay -> DUNG TOAN BO"
  return 1
}

# Replicat TRUOC (that bai lanh hon), Extract SAU
if ! restart_one "$REP" REPLICAT; then
  echo; echo "!!! DUNG. Rollback: cp -p dirprm/<file>.bak_<TS> dirprm/<file> roi start lai."
  exit 1
fi
if ! restart_one "$EXT" EXTRACT; then
  echo; echo "!!! DUNG. Rollback: cp -p dirprm/<file>.bak_<TS> dirprm/<file> roi start lai."
  exit 1
fi

# ---------------------------------------------------------------------
hdr "C. BANG CHUNG SAU KHI RESTART"
sub "info all SAU"
./ggsci <<'GGEOF' 2>/dev/null | grep -E "MANAGER|EXTRACT|REPLICAT"
info all
GGEOF
trailinfo
sub "$REP detail"; pdetail "$REP" | indent
sub "$EXT detail"; pdetail "$EXT" | indent
sub "DB evidence";  evidence | indent

hdr "D. KIEM TOAN LUONG $STREAM"
echo "  discard: RPMNVODS=$(stat -c%s dirrpt/RPMNVODS.dsc) RHT=$(stat -c%s dirrpt/RHT.dsc) RDTHU=$(stat -c%s dirrpt/RDTHU.dsc)  (phai 118/113/115)"
D=$(stat -c%s dirrpt/${REP}.dsc)
if [ "$D" -eq "$DSC" ]; then
  echo "  [ OK ] ${REP}.dsc = $D dung moc rong"
else
  echo "  [FAIL] ${REP}.dsc = $D, mong doi $DSC -> CO DISCARD MOI"
fi
echo "  abend/fatal hom nay (bo QU_ERROR): $(grep "$(date +%Y-%m-%d)" ggserr.log 2>/dev/null | grep -E "ABEND|FATAL" | grep -vi QU_ERROR | wc -l)"
sub "ggserr.log 10 dong cuoi lien quan $EXT / $REP"
grep -E "$EXT|$REP" ggserr.log | tail -10 | indent
echo
echo "  >>> Luong $STREAM da restart xong bang USERIDALIAS."
echo "  >>> Ra soat bang chung tren roi chay luong tiep theo."
exit 0
