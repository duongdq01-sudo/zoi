#!/bin/bash
# =====================================================================
# cs9_grant_ogg_bsh.sh - vo boc cho bo script quyen cua OGG_BSH
#
# CHAY TREN: server GG 172.16.33.90, user `oracle`
#
# CACH CHAY
#   bash cs9_grant_ogg_bsh.sh                # chi in ke hoach, khong chay gi
#   bash cs9_grant_ogg_bsh.sh --review-tgt   # ra soat quyen o DICH (chi doc)
#   bash cs9_grant_ogg_bsh.sh --test         # chay phep thu no-op o NGUON
#   bash cs9_grant_ogg_bsh.sh --check-dsc    # chi xem 3 discard file
#
# KHONG co che do nao cua script nay chay GRANT. Ly do: GRANT phai chay
# bang sysdba o DB NGUON 172.16.33.68, ma tu server nay khong co sysdba
# o nguon. Phan do la file .sql rieng, dua cho DBA nguon chay.
#
# Thu tu dung:
#   1) DBA nguon : sqlplus / as sysdba @cs9_grant_ogg_bsh_src.sql
#   2) o day     : bash cs9_grant_ogg_bsh.sh --test
#   3) DBA nguon : sqlplus / as sysdba @cs9_revoke_ogg_bsh_src.sql
# =====================================================================
set -uo pipefail

SELF=$(cd "$(dirname "$0")" && pwd)
SEC=/u01/app/ggadmin/conf/secrets.ini
GG=/u01/app/ogg
DSC="$GG/dirrpt/RHT.dsc"
MOC_RHT=113                 # moc rong cua RHT.dsc, da do that
SRC_DSN='PMNV'              # alias TNS -> 172.16.33.68
MODE="${1:-}"

# `ssh host 'cmd'` khong source .bash_profile, nen phai tu source. set +u
# quanh lenh source: trong profile co bien chua dat, `set -u` se lam script
# thoat IM LANG khong in gi -- da mac loi nay that.
set +u; . ~/.bash_profile 2>/dev/null || true; set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8

hdr() { echo; echo "############ $* ############"; }

# ---------------------------------------------------------------- ke hoach
if [ -z "$MODE" ]; then
  cat <<'TXT'

BO SCRIPT QUYEN CHO OGG_BSH
===========================

A. O DB NGUON PMNV (172.16.33.68)  --  DA CHOT 2026-10-14: KHONG DOI GI
   Quyen cua OGG_BSH o nguon la BAN CUOI. Khong grant, khong revoke.
   Hai file duoi day GIU LAI de tham khao, KHONG CHAY:
     cs9_grant_ogg_bsh_src.sql    (parked)
     cs9_revoke_ogg_bsh_src.sql   (parked)
     cs9_test_noop_pvi.sql        (parked -- can quyen UPDATE khong duoc cap)

   He qua: phep thu no-op UPDATE KHONG chay duoc. OGG_BSH o nguon co
   SELECT ANY TABLE nhung KHONG co UPDATE/INSERT/DELETE ANY TABLE va khong
   co quyen object nao tren KT_BSH -- tuc la no KHONG SUA duoc du lieu
   nguon. Day la tinh chat tot, giu nguyen.

   Cau hoi "PHONG NULL co lam Replicat bo im lang khong" chuyen tu THU
   CHU DONG sang GIAM SAT:
     - verify hang tuan: 2 bang nay nam trong 18 bang luong HT, lan verify
       gan nhat 18/18 KHOP tai SCN 150502846
     - luat canh bao `discard_lech_moc`: RHT.dsc khac 113 byte la bao ngay
     Neu bao gio co UPDATE vao dong PHONG NULL ma bi discard, hai cai tren
     se tu noi ra. Khong can quyen gi them.

B. O DB DICH BSHC (172.16.33.90) -- chay duoc ngay tai day
   1. cs9_review_ogg_bsh_tgt.sql
        CHI DOC. Ra soat quyen OGG_BSH va SINH RA lenh REVOKE cho 3 quyen
        khong can: ALTER SYSTEM, DROP ANY TABLE, DROP ANY EDITION.
        Script khong tu chay REVOKE.

LENH CHAY
   bash cs9_grant_ogg_bsh.sh --review-tgt     # ra soat quyen o dich
   bash cs9_grant_ogg_bsh.sh --check-dsc      # xem 3 discard file
   bash cs9_grant_ogg_bsh.sh --test           # DA KHOA, xem muc A

LUU Y QUYEN
   KHONG cap ANALYZE ANY cho OGG_BSH. Gather statistics o ODS chay bang
   chinh chu schema ODS -- chu schema luon lam duoc tren object cua minh.

TXT
  echo "File .sql o: $SELF"
  ls -1 "$SELF"/cs9_*.sql 2>/dev/null | sed 's|.*/|  |'
  echo
  exit 0
fi

# ------------------------------------------------------------ xem discard
xem_dsc() {
  hdr "Discard file"
  for f in RPMNVODS RHT RDTHU; do
    p="$GG/dirrpt/$f.dsc"
    if [ -f "$p" ]; then
      printf '  %-10s %8s byte\n' "$f" "$(stat -c%s "$p")"
    else
      printf '  %-10s khong co file\n' "$f"
    fi
  done
  echo "  (moc rong: RPMNVODS 118 / RHT 113 / RDTHU 115)"
}

if [ "$MODE" = "--check-dsc" ]; then
  xem_dsc
  echo
  exit 0
fi

# --------------------------------------------------------- ra soat o dich
if [ "$MODE" = "--review-tgt" ]; then
  hdr "Ra soat quyen OGG_BSH o DB dich (chi doc)"
  sqlplus -S / as sysdba @"$SELF/cs9_review_ogg_bsh_tgt.sql"
  echo
  exit 0
fi

# ------------------------------------------------------------- phep thu
if [ "$MODE" != "--test" ]; then
  echo "Khong hieu tham so: $MODE" >&2
  echo "Dung: bash cs9_grant_ogg_bsh.sh [--review-tgt|--test|--check-dsc]" >&2
  exit 2
fi

if [ "${CS9_CHO_PHEP_TEST:-0}" != "1" ]; then
  cat <<'TXT'

  --test DA KHOA.

  Quyen cua OGG_BSH o nguon da chot 2026-10-14 la ban cuoi, khong grant
  them. Phep thu no-op can quyen UPDATE tren KT_BSH ma OGG_BSH khong co
  (co SELECT ANY TABLE, KHONG co UPDATE ANY TABLE, khong co quyen object
  nao tren KT_BSH) -- chay vao se chet giua duong voi ORA-01031 sau khi
  da doi mat khau ra ngoai.

  Thay vao do dung giam sat:
    bash cs9_grant_ogg_bsh.sh --check-dsc
    verify luong HT trong ggadmin  (2 bang *_PVI nam trong 18 bang do)

  Neu that su da co quyen UPDATE roi thi mo khoa bang:
    CS9_CHO_PHEP_TEST=1 bash cs9_grant_ogg_bsh.sh --test

TXT
  exit 1
fi

hdr "0. Lay mat khau OGG_BSH o nguon tu secrets.ini"
[ -f "$SEC" ] || { echo "  [FAIL] khong thay $SEC"; exit 1; }
PW=$(awk -F= '/^\[ogg_source\]/{f=1} f&&/^password/{gsub(/^[ \t]+|[ \t]+$/,"",$2);print $2;exit}' "$SEC")
[ -n "$PW" ] || { echo "  [FAIL] khong doc duoc mat khau tu [ogg_source]"; exit 1; }
echo "  [ OK ] da doc (khong in ra)"
mask() { sed -e "s/$PW/***/g"; }

hdr "1. Trang thai TRUOC phep thu"
cd "$GG" || exit 1
printf 'info all
' | ./ggsci 2>/dev/null   | awk '/^MANAGER|^EXTRACT|^REPLICAT/' | sed 's/^/  /'
TRUOC=$(stat -c%s "$DSC" 2>/dev/null || echo 0)
echo "  RHT.dsc = $TRUOC byte (moc rong $MOC_RHT)"
if [ "$TRUOC" -ne "$MOC_RHT" ]; then
  echo
  echo "  [DUNG LAI] RHT.dsc da KHAC moc rong truoc khi thu."
  echo "  Chay phep thu luc nay thi khong biet byte moi den tu dau."
  echo "  Xem truoc:  tail -40 $DSC"
  exit 1
fi

hdr "2. Chay no-op UPDATE o nguon (bang OGG_BSH@$SRC_DSN)"
sqlplus -S "OGG_BSH/$PW@$SRC_DSN" @"$SELF/cs9_test_noop_pvi.sql" 2>&1 | mask | sed 's/^/  /'
RC=${PIPESTATUS[0]}
if [ "$RC" -ne 0 ]; then
  echo
  echo "  [FAIL] sqlplus tra ma $RC. Thuong la chua chay cs9_grant_ogg_bsh_src.sql"
  echo "         (ORA-01031 insufficient privileges tren UPDATE)."
  exit 1
fi

hdr "3. Cho Replicat apply, theo doi RHT.dsc toi 120 giay"
# Khong `sleep` co dinh: poll. Lag CHI doc tu `info all` -- `info ... detail`
# co dong Last Started lam cot 4 thanh chu `Started` (cam bay 26).
LAG0=0
SAU="$TRUOC"
for i in $(seq 1 20); do
  sleep 6
  SAU=$(stat -c%s "$DSC" 2>/dev/null || echo 0)
  LAG=$(printf 'info all
' | ./ggsci 2>/dev/null           | awk '$3=="RHT"{print $4; exit}')
  LAG="${LAG:-?}"
  printf '  %3ds  lag=%-10s RHT.dsc=%s byte\n' "$((i*6))" "$LAG" "$SAU"

  if [ "$SAU" -ne "$TRUOC" ]; then
    echo "  -> discard file da to len, dung cho."
    break
  fi
  if [ "$LAG" = "00:00:00" ]; then
    LAG0=$((LAG0+1))
    [ "$LAG0" -ge 3 ] && { echo "  -> lag 00:00:00 ba lan lien, dung cho."; break; }
  else
    LAG0=0
  fi
done

hdr "4. KET LUAN"
echo "  RHT.dsc: $TRUOC -> $SAU byte"
if [ "$SAU" -eq "$TRUOC" ]; then
  cat <<'TXT'

  KHONG co gi bi discard.

  Nghia la Replicat TIM THAY dong ke ca khi cot KEYCOLS la NULL, hoac
  ban ghi no-op khong di tiep toi dich. Phai phan biet hai kha nang do
  truoc khi ket luan -- xem `stats replicat rht` ben duoi: neu so UPDATE
  tang dung bang so ca da chay thi la kha nang thu nhat.
TXT
  printf 'dblogin useridalias bshc_ogg
stats replicat rht, latest, table kt_bsh.ht_nsd_pvi
'     | ./ggsci 2>/dev/null     | grep -iE "total|update|insert|delete|discard" | head -20 | sed 's/^/    /'
else
  echo
  echo "  CO ban ghi bi discard. $((SAU - TRUOC)) byte moi."
  echo
  echo "  Noi dung moi trong RHT.dsc:"
  tail -c "$((SAU - TRUOC + 400))" "$DSC" | sed 's/^/    /'
  cat <<'TXT'

  Doc ky phan tren:
   - Chi ban ghi cua ca A (PHONG IS NULL) bi discard, kem ORA-01403
     -> gia thuyet DUNG: KEYCOLS co cot NULL thi Replicat khong tim thay
        dong, va REPERROR (1403, DISCARD) bo IM LANG.
   - Ca ca B cung bi discard -> van de KHONG phai do NULL, phai dieu tra lai.
TXT
fi

echo
echo "  NHO: go quyen tam thoi o nguon -- cs9_revoke_ogg_bsh_src.sql"
echo
