#!/bin/bash
# =====================================================================
# cs1_create.sh - BUOC 1/4: lap GG credential store + conf/secrets.ini
#
# KHONG sua param file. KHONG restart process nao. Chi THEM thu.
# Muc dich: chung minh alias hoat dong TRUOC khi cham vao param file,
# va tao cho chua mat khau moi TRUOC khi xoa mat khau khoi param file.
#
# Vi sao phai co secrets.ini: moi script van hanh hien tai deu lam
#     PW_TGT=$(grep -iE '^USERID .*OGG_BSH@BSHC' dirprm/rpmnvods.prm | sed ...)
# Xoa mat khau khoi *.prm la lam HONG het nhung script do. Nen phai co
# nguon thay the truoc, roi moi xoa.
# =====================================================================
set -uo pipefail

GGHOME=/u01/app/ogg
GGADMIN=/u01/app/ggadmin
ALIAS_SRC=pmnv_ogg
ALIAS_TGT=bshc_ogg

set +u; . ~/.bash_profile 2>/dev/null || true; set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8
cd "$GGHOME" || exit 1

# Loc moi dong co the chua mat khau ra khoi output
mask() { grep -viE "post-quantum|store now|upgraded" | sed -E 's/(PASSWORD[[:space:]]+)[^ ,]+/\1***/Ig'; }
hdr()  { echo; echo "############ $* ############"; }
FAILED=0

hdr "0. Trang thai TRUOC khi lam gi"
./ggsci <<'EOF' 2>/dev/null | grep -E "MANAGER|EXTRACT|REPLICAT"
info all
EOF
echo "  dircrd hien tai: $(ls -1A "$GGHOME/dircrd" 2>/dev/null | wc -l) file"
echo "  param file con mat khau dang ro: $(grep -ilE '^USERID.*PASSWORD' "$GGHOME"/dirprm/*.prm 2>/dev/null | wc -l) / $(ls -1 "$GGHOME"/dirprm/*.prm | wc -l)"
echo "  GLOBALS: $( [ -s "$GGHOME/GLOBALS" ] && echo "co noi dung" || echo "RONG -> credential store dung mac dinh dircrd/" )"

# ---------------------------------------------------------------------
hdr "1. Doc mat khau hien tai tu param file (lan cuoi)"
PW_SRC=$(grep -ihE '^USERID[[:space:]]+OGG_BSH@PMNV' "$GGHOME"/dirprm/etpmnv.prm \
         | sed -E 's/.*PASSWORD[[:space:]]+//I; s/[[:space:]]*$//; s/,$//' | head -1)
PW_TGT=$(grep -ihE '^USERID[[:space:]]+OGG_BSH@BSHC' "$GGHOME"/dirprm/rpmnvods.prm \
         | sed -E 's/.*PASSWORD[[:space:]]+//I; s/[[:space:]]*$//; s/,$//' | head -1)
[ -n "$PW_SRC" ] && echo "  [ OK ] lay duoc mat khau @PMNV (${#PW_SRC} ky tu, khong in ra)" || { echo "  [FAIL] khong lay duoc @PMNV"; exit 1; }
[ -n "$PW_TGT" ] && echo "  [ OK ] lay duoc mat khau @BSHC (${#PW_TGT} ky tu, khong in ra)" || { echo "  [FAIL] khong lay duoc @BSHC"; exit 1; }

# Mat khau co giong nhau o 2 dau khong - de biet khi viet secrets.ini
[ "$PW_SRC" = "$PW_TGT" ] && echo "  (hai dau dung CUNG mat khau)" || echo "  (hai dau dung mat khau KHAC nhau)"

# ---------------------------------------------------------------------
hdr "2. Tao conf/secrets.ini (0600) - nguon mat khau thay the cho *.prm"
mkdir -p "$GGADMIN/conf"
SEC="$GGADMIN/conf/secrets.ini"
if [ -f "$SEC" ]; then
  cp -p "$SEC" "$SEC.bak_$(date +%Y%m%d_%H%M%S)"
  echo "  (da co, backup lai truoc khi ghi de)"
fi
umask 077
cat > "$SEC" <<SECEOF
# ggadmin - mat khau DB. chmod 0600, owner oracle.
# Sinh boi tools/cs1_create.sh $(date '+%Y-%m-%d %H:%M:%S')
#
# Vi sao file nay ton tai: sau khi doi *.prm sang USERIDALIAS thi mat khau
# KHONG con trong param file nua. ggsci dung credential store (alias),
# nhung sqlplus va python van can mat khau -> doc tu day.
#
# oracledb thin mode KHONG doc duoc Oracle Wallet / external password store,
# nen dung file 0600 do oracle so huu. Ranh gioi tin cay khong doi: user
# oracle truoc gio van doc duoc *.prm.

[ogg_source]
user = OGG_BSH
dsn  = PMNV
password = $PW_SRC

[ogg_target]
user = OGG_BSH
dsn  = BSHC
password = $PW_TGT
SECEOF
chmod 600 "$SEC"
ls -la "$SEC" | sed 's/^/  /'
echo "  [ OK ] da ghi, quyen $(stat -c%a "$SEC")"

# Doc lai tu secrets.ini de chung minh no dung duoc
PW_CHK=$(awk -F'=' '/^\[ogg_target\]/{f=1} f&&/^password/{gsub(/^[ \t]+|[ \t]+$/,"",$2);print $2;exit}' "$SEC")
[ "$PW_CHK" = "$PW_TGT" ] && echo "  [ OK ] doc lai tu secrets.ini khop" || { echo "  [FAIL] doc lai KHONG khop"; FAILED=$((FAILED+1)); }

# ---------------------------------------------------------------------
hdr "3. ADD CREDENTIALSTORE + 2 alias"
# Luu y: dircrd/ da ton tai nhung RONG. ADD CREDENTIALSTORE tao cwallet.sso.
./ggsci <<EOF 2>&1 | mask
add credentialstore
EOF

./ggsci <<EOF 2>&1 | mask | grep -viE "^ALTER CREDENTIALSTORE"
alter credentialstore add user OGG_BSH@PMNV password $PW_SRC alias $ALIAS_SRC
alter credentialstore add user OGG_BSH@BSHC password $PW_TGT alias $ALIAS_TGT
EOF

echo "  --- INFO CREDENTIALSTORE ---"
./ggsci <<'EOF' 2>&1 | mask | grep -E "Domain|Alias|Userid"
info credentialstore
EOF

echo "  --- file trong dircrd ---"
ls -la "$GGHOME/dircrd/" | tail -n +2 | sed 's/^/    /'
# cwallet.sso mat la CA 6 PROCESS khong start duoc -> backup ngay
if [ -f "$GGHOME/dircrd/cwallet.sso" ]; then
  mkdir -p "$GGADMIN/backup"
  cp -p "$GGHOME/dircrd/cwallet.sso" "$GGADMIN/backup/cwallet.sso.$(date +%Y%m%d_%H%M%S)"
  echo "  [ OK ] da backup cwallet.sso -> $GGADMIN/backup/"
else
  echo "  [FAIL] khong thay dircrd/cwallet.sso"; FAILED=$((FAILED+1))
fi

# ---------------------------------------------------------------------
hdr "4. CHUNG MINH alias dung duoc - TRUOC khi cham param file"
# Day la cong kiem quan trong nhat cua buoc 1. Neu alias sai thi ca 6
# process se ABEND khi start. Phai biet NGAY O DAY, luc chua sua gi.

echo "  --- alias NGUON: dblogin + mot lenh doi hoi dang nhap ---"
OUT_SRC=$(./ggsci <<EOF 2>&1 | mask
dblogin useridalias $ALIAS_SRC
info trandata KT_BSH.HT_MA_DVI
EOF
)
echo "$OUT_SRC" | grep -E "Successfully logged|ERROR|OGG-[0-9]+|supplemental" | sed 's/^/    /'
if echo "$OUT_SRC" | grep -qi "Successfully logged into database"; then
  echo "  [ OK ] alias $ALIAS_SRC dang nhap duoc @PMNV"
else
  echo "  [FAIL] alias $ALIAS_SRC KHONG dang nhap duoc"; FAILED=$((FAILED+1))
fi

echo "  --- alias DICH: dblogin + doc checkpoint table ---"
OUT_TGT=$(./ggsci <<EOF 2>&1 | mask
dblogin useridalias $ALIAS_TGT
info checkpointtable OGG_BSH.RHT
EOF
)
echo "$OUT_TGT" | grep -E "Successfully logged|ERROR|OGG-[0-9]+|Checkpoint table" | sed 's/^/    /'
if echo "$OUT_TGT" | grep -qi "Successfully logged into database"; then
  echo "  [ OK ] alias $ALIAS_TGT dang nhap duoc @BSHC"
else
  echo "  [FAIL] alias $ALIAS_TGT KHONG dang nhap duoc"; FAILED=$((FAILED+1))
fi

# ---------------------------------------------------------------------
hdr "5. Khang dinh KHONG co gi bi thay doi o buoc nay"
./ggsci <<'EOF' 2>/dev/null | grep -E "MANAGER|EXTRACT|REPLICAT"
info all
EOF
echo "  param file con mat khau dang ro: $(grep -ilE '^USERID.*PASSWORD' "$GGHOME"/dirprm/*.prm 2>/dev/null | wc -l) / 7  (chua doi - dung nhu mong doi)"
echo "  RPMNVODS.dsc=$(stat -c%s "$GGHOME/dirrpt/RPMNVODS.dsc") RHT.dsc=$(stat -c%s "$GGHOME/dirrpt/RHT.dsc") RDTHU.dsc=$(stat -c%s "$GGHOME/dirrpt/RDTHU.dsc")  (phai 118/113/115)"
echo "  abend/fatal hom nay: $(grep "$(date +%Y-%m-%d)" "$GGHOME/ggserr.log" 2>/dev/null | grep -E "ABEND|FATAL" | grep -vi "QU_ERROR" | wc -l)"

hdr "KET QUA BUOC 1"
if [ "$FAILED" -eq 0 ]; then
  echo "  OK - credential store da lap, 2 alias da chung minh dang nhap duoc."
  echo "  Chua sua param file, chua restart gi. San sang sang buoc 2."
else
  echo "  CO $FAILED MUC FAIL - DUNG LAI, khong sang buoc 2."
fi
exit "$FAILED"
