#!/bin/bash
# =====================================================================
# cs2_prm.sh - BUOC 2/4: doi 6 param file sang USERIDALIAS
#
# MAC DINH LA DRY-RUN: chi in ra se doi gi, khong ghi gi.
# Chi sua that khi truyen --apply.
#
# KHONG restart process nao. Process dang chay van dung cau hinh cu
# trong bo nho -> sua param file luc nay khong anh huong gi.
#
# Cong kiem: neu bat ky checkprm nao FAIL -> tu dong restore CA 6 file.
# =====================================================================
set -uo pipefail

GGHOME=/u01/app/ogg
ALIAS_SRC=pmnv_ogg
ALIAS_TGT=bshc_ogg
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
TS=$(date +%Y%m%d_%H%M%S)

set +u; . ~/.bash_profile 2>/dev/null || true; set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8
cd "$GGHOME" || exit 1

mask() { sed -E 's/(PASSWORD[[:space:]]+)[^ ,]+/\1***/Ig'; }
hdr()  { echo; echo "############ $* ############"; }

TARGETS=(
  "ETPMNV:etpmnv.prm:EXTRACT:$ALIAS_SRC"
  "ETHT:etht.prm:EXTRACT:$ALIAS_SRC"
  "ETDTHU:etdthu.prm:EXTRACT:$ALIAS_SRC"
  "RPMNVODS:rpmnvods.prm:REPLICAT:$ALIAS_TGT"
  "RHT:rht.prm:REPLICAT:$ALIAS_TGT"
  "RDTHU:rdthu.prm:REPLICAT:$ALIAS_TGT"
)

restore_all() {
  echo
  echo "  !!! ROLLBACK: restore ca 6 param file tu .bak_$TS"
  for t in "${TARGETS[@]}"; do
    f=$(echo "$t" | cut -d: -f2)
    [ -f "dirprm/$f.bak_$TS" ] && cp -p "dirprm/$f.bak_$TS" "dirprm/$f" && echo "    restored dirprm/$f"
  done
  echo "  Process dang chay KHONG bi anh huong (chung doc param luc start)."
}

if [ "$APPLY" -eq 1 ]; then
  echo "### CHE DO: --apply  (SE GHI THAT) ###"
else
  echo "### CHE DO: DRY-RUN  (khong ghi gi; them --apply de sua that) ###"
fi

hdr "0. Tien dieu kien"
if [ ! -f "$GGHOME/dircrd/cwallet.sso" ]; then
  echo "  [FAIL] chua co credential store -> chay cs1_create.sh truoc"; exit 1
fi
echo "  [ OK ] dircrd/cwallet.sso ton tai"
echo "  --- dong USERID hien tai / se thanh gi ---"
for t in "${TARGETS[@]}"; do
  f=$(echo "$t" | cut -d: -f2); al=$(echo "$t" | cut -d: -f4)
  CUR=$(grep -ihE '^USERID' "dirprm/$f" | mask)
  printf "    %-14s  %-46s  =>  USERIDALIAS %s\n" "$f" "$CUR" "$al"
done

if [ "$APPLY" -eq 0 ]; then
  hdr "DRY-RUN KET THUC"
  echo "  Khong file nao bi sua. Chay lai voi --apply de thuc hien."
  exit 0
fi

hdr "1. Backup ca 6 file"
for t in "${TARGETS[@]}"; do
  f=$(echo "$t" | cut -d: -f2)
  cp -p "dirprm/$f" "dirprm/$f.bak_$TS" || { echo "  [FAIL] khong backup duoc $f"; exit 1; }
  printf "    dirprm/%-14s -> %s.bak_%s  (mode %s)\n" "$f" "$f" "$TS" "$(stat -c%a "dirprm/$f")"
done

hdr "2. Thay USERID ... PASSWORD ... -> USERIDALIAS"
for t in "${TARGETS[@]}"; do
  f=$(echo "$t" | cut -d: -f2); al=$(echo "$t" | cut -d: -f4)
  MODE=$(stat -c%a "dirprm/$f")
  sed -i -E "s|^USERID[[:space:]]+OGG_BSH@[A-Za-z0-9_]+.*$|USERIDALIAS $al|I" "dirprm/$f"
  chmod "$MODE" "dirprm/$f"
  N_ALIAS=$(grep -cE '^USERIDALIAS ' "dirprm/$f" || true)
  N_PLAIN=$(grep -ciE '^USERID[[:space:]].*PASSWORD' "dirprm/$f" || true)
  printf "    %-14s USERIDALIAS=%s plaintext=%s  -> %s\n" \
    "$f" "$N_ALIAS" "$N_PLAIN" "$(grep -E '^USERIDALIAS ' "dirprm/$f")"
  if [ "$N_ALIAS" -ne 1 ] || [ "$N_PLAIN" -ne 0 ]; then
    echo "  [FAIL] $f khong dung ky vong (can USERIDALIAS=1, plaintext=0)"
    restore_all; exit 1
  fi
done

hdr "3. Khang dinh khong con mat khau dang ro"
LEFT=$(grep -ilE '^USERID[[:space:]].*PASSWORD' "$GGHOME"/dirprm/*.prm 2>/dev/null | wc -l)
echo "  so file .prm con mat khau dang ro: $LEFT  (muc tieu 0)"
if [ "$LEFT" -ne 0 ]; then
  grep -ilE '^USERID[[:space:]].*PASSWORD' "$GGHOME"/dirprm/*.prm | sed 's/^/    con: /'
  restore_all; exit 1
fi
echo "  [ OK ] 0 file"
chmod 600 "$GGHOME"/dirprm/*.bak_* 2>/dev/null || true
echo "  (file .bak_* con mat khau la CO Y de rollback duoc; da chmod 600)"

hdr "4. checkprm ca 6 file"
BAD=0
for t in "${TARGETS[@]}"; do
  f=$(echo "$t" | cut -d: -f2); ty=$(echo "$t" | cut -d: -f3)
  OUT=$(./checkprm "dirprm/$f" -c "$ty" 2>&1 | mask)
  if echo "$OUT" | grep -qiE "error|invalid|unrecognized|OGG-100[0-9][0-9]"; then
    printf "    %-14s %-9s FAIL\n" "$f" "$ty"
    echo "$OUT" | grep -iE "error|invalid|unrecognized|OGG-" | head -4 | sed 's/^/        /'
    BAD=$((BAD+1))
  else
    printf "    %-14s %-9s PASS\n" "$f" "$ty"
  fi
done
if [ "$BAD" -ne 0 ]; then echo "  [FAIL] $BAD file khong qua checkprm"; restore_all; exit 1; fi
echo "  [ OK ] ca 6 file qua checkprm"

hdr "5. He thong van chay binh thuong (chua restart)"
./ggsci <<'GGEOF' 2>/dev/null | grep -E "MANAGER|EXTRACT|REPLICAT"
info all
GGEOF
echo "  RPMNVODS.dsc=$(stat -c%s dirrpt/RPMNVODS.dsc) RHT.dsc=$(stat -c%s dirrpt/RHT.dsc) RDTHU.dsc=$(stat -c%s dirrpt/RDTHU.dsc)  (phai 118/113/115)"
echo "  abend/fatal hom nay: $(grep "$(date +%Y-%m-%d)" ggserr.log 2>/dev/null | grep -E "ABEND|FATAL" | grep -vi "QU_ERROR" | wc -l)"
echo
echo "  LUU Y: 6 process dang chay VAN dung cau hinh CU trong bo nho."
echo "  Cau hinh moi chi co hieu luc sau khi restart (buoc 3)."
echo "  TS backup de rollback: $TS"

hdr "KET QUA BUOC 2"
echo "  OK - 6 param file da sang USERIDALIAS, 0 mat khau dang ro, checkprm PASS."
echo "  Rollback: cp -p dirprm/<file>.bak_$TS dirprm/<file>"
exit 0
