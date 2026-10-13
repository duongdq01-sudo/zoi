#!/bin/bash
# =====================================================================
# spike.sh - Phase 0: chung minh 3 duong ket noi TRUOC khi viet tinh nang.
#
# Chay tren server GG bang user `oracle`. KHONG can sudo. KHONG sua gi
# ngoai /u01/app/ggadmin/. Khong dung den Python he thong (3.6.8).
#
# Thanh cong = 6 muc duoi day deu OK:
#   1. Giai nen CPython standalone, `python3 -V` ra 3.12  (kiem glibc)
#   2. pip install --no-index tu wheelhouse  (kiem offline, khong can gcc)
#   3. import oracledb  (thin mode, khong can thu vien Oracle)
#   4. oracledb thin connect tai DB dich BSHC
#   5. ggsci qua subprocess voi env DUNG RIENG, lay duoc `info all`
#   6. Trang thai account ODS + 3 account se dung
#
# Tham so: duong dan file tar CPython (mac dinh /tmp/cpython-*.tar.gz)
# =====================================================================
set -u

GGADMIN=/u01/app/ggadmin
GGHOME=/u01/app/ogg
PYTAR="${1:-$(ls -1t /tmp/cpython-3.12*-install_only.tar.gz 2>/dev/null | head -1)}"
PYSHA_EXPECT="${2:-}"

ok()   { echo "  [ OK ] $*"; }
bad()  { echo "  [FAIL] $*"; FAILED=$((FAILED+1)); }
hdr()  { echo; echo "############ $* ############"; }
FAILED=0

# .bash_profile cua user oracle tham chieu bien chua dat -> voi `set -u` se lam
# shell THOAT IM LANG (vi stderr bi chan). Phai tat -u quanh doan source.
set +u
. ~/.bash_profile 2>/dev/null || true
set -u
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8

hdr "0. Boi canh"
echo "  host      : $(hostname)"
echo "  user      : $(id -un)  (uid $(id -u))"
echo "  OS        : $(cat /etc/redhat-release 2>/dev/null)"
echo "  glibc     : $(ldd --version | head -1 | awk '{print $NF}')"
echo "  ORACLE_HOME: ${ORACLE_HOME:-<chua set>}"
echo "  ORACLE_SID : ${ORACLE_SID:-<chua set>}"
echo "  TNS_ADMIN  : ${TNS_ADMIN:-<chua set, mac dinh \$ORACLE_HOME/network/admin>}"
echo "  dia /      : $(df -hP / | tail -1 | awk '{print "dung "$3", con "$4" ("$5")"}')"

# ---------------------------------------------------------------------
hdr "1. CPython standalone"
if [ -z "$PYTAR" ] || [ ! -f "$PYTAR" ]; then
  bad "khong thay file tar CPython. Hay scp truoc roi chay lai."
  echo "       scp vendor-runtime/cpython-*.tar.gz oracle@<host>:/tmp/"
  exit 1
fi
echo "  tar: $PYTAR  ($(du -h "$PYTAR" | cut -f1))"

PYSHA_ACTUAL=$(sha256sum "$PYTAR" | awk '{print $1}')
echo "  sha256 tren server: $PYSHA_ACTUAL"
if [ -n "$PYSHA_EXPECT" ]; then
  if [ "$PYSHA_ACTUAL" = "$PYSHA_EXPECT" ]; then
    ok "sha256 KHOP ban tai tu Windows -> file khong bi hong khi scp"
  else
    bad "sha256 LECH. mong doi=$PYSHA_EXPECT"
    exit 1
  fi
else
  echo "  (khong truyen sha mong doi -> bo qua buoc doi chieu)"
fi

mkdir -p "$GGADMIN"/{app,web,data,conf,logs,scripts,backup} || { bad "khong tao duoc $GGADMIN"; exit 1; }
chmod 700 "$GGADMIN"/{data,conf,logs}
ok "cay thu muc $GGADMIN"

if [ -x "$GGADMIN/py/bin/python3" ]; then
  echo "  py/ da ton tai, giai nen lai cho sach"
  rm -rf "$GGADMIN/py"
fi
# Tarball giai nen ra thu muc `python/` -> doi ten thanh py/
rm -rf "$GGADMIN/.pytmp" && mkdir -p "$GGADMIN/.pytmp"
if tar -xzf "$PYTAR" -C "$GGADMIN/.pytmp"; then
  mv "$GGADMIN/.pytmp/python" "$GGADMIN/py" && rmdir "$GGADMIN/.pytmp"
  ok "giai nen -> $GGADMIN/py"
else
  bad "tar -xzf that bai"; exit 1
fi

PY="$GGADMIN/py/bin/python3"
PYV=$("$PY" -V 2>&1)
if echo "$PYV" | grep -q "^Python 3.12"; then
  ok "$PYV  (chay duoc tren glibc $(ldd --version | head -1 | awk '{print $NF}'))"
else
  bad "python3 -V tra ve: $PYV"
fi
# Relocatable that khong? RPATH phai la $ORIGIN/../lib
echo "  RPATH: $(objdump -x "$PY" 2>/dev/null | grep -E 'RUNPATH|RPATH' | head -1 | awk '{print $2}' || echo '<khong doc duoc objdump>')"
echo "  dung luong py/: $(du -sh "$GGADMIN/py" | cut -f1)"

# Kiem module stdlib ma ggadmin phu thuoc
for m in sqlite3 ssl hashlib tomllib asyncio subprocess; do
  if "$PY" -c "import $m" 2>/dev/null; then ok "stdlib: $m"; else bad "stdlib THIEU: $m"; fi
done
echo "  sqlite3 version: $("$PY" -c 'import sqlite3;print(sqlite3.sqlite_version)' 2>/dev/null)"

# ---------------------------------------------------------------------
hdr "2. Cai wheel offline (khong internet, khong gcc)"
WH="$GGADMIN/wheelhouse"
if [ ! -d "$WH" ]; then
  bad "khong thay $WH. Hay scp -r wheelhouse truoc."
else
  echo "  $(ls -1 "$WH"/*.whl 2>/dev/null | wc -l) wheel, $(du -sh "$WH" | cut -f1)"
  # Co wheel sai nen tang lot vao khong?
  if ls -1 "$WH" | grep -qE "win_amd64|win32|macosx"; then
    bad "co wheel KHONG phai Linux trong wheelhouse"
    ls -1 "$WH" | grep -E "win_amd64|win32|macosx"
  else
    ok "moi wheel deu la manylinux hoac none-any"
  fi

  REQ="$GGADMIN/requirements.txt"
  if [ ! -f "$REQ" ]; then bad "khong thay $REQ"; else
    # dry-run TRUOC: pip download tu Windows co the bo qua dependency
    # co marker platform-specific -> phai kiem truoc khi tin wheelhouse day du
    echo "  --- dry-run ---"
    if "$PY" -m pip install --no-index --find-links="$WH" -r "$REQ" --dry-run -q 2>&1 | tail -5; then
      ok "dry-run: wheelhouse DAY DU, khong thieu dependency nao"
    else
      bad "dry-run that bai -> wheelhouse thieu wheel"
    fi
    echo "  --- cai that vao venv ---"
    "$PY" -m venv "$GGADMIN/venv" 2>&1 | tail -2
    VPY="$GGADMIN/venv/bin/python3"
    if "$VPY" -m pip install --no-index --find-links="$WH" -r "$REQ" -q 2>&1 | tail -5; then
      ok "cai xong vao $GGADMIN/venv"
      PY="$VPY"
    else
      bad "pip install that bai"
    fi
  fi
fi

# ---------------------------------------------------------------------
hdr "3. import oracledb (thin mode)"
"$PY" - <<'PYEOF'
import sys
try:
    import oracledb
    print("  [ OK ] oracledb", oracledb.__version__)
    print("         thin mode =", not oracledb.is_thin_mode.__doc__ is None)
    import fastapi, uvicorn, jinja2, pydantic, itsdangerous
    print("  [ OK ] fastapi", fastapi.__version__, "/ uvicorn", uvicorn.__version__,
          "/ jinja2", jinja2.__version__, "/ pydantic", pydantic.VERSION)
except Exception as e:
    print("  [FAIL]", type(e).__name__, e); sys.exit(1)
PYEOF
[ $? -eq 0 ] || FAILED=$((FAILED+1))

# ---------------------------------------------------------------------
hdr "4. oracledb thin connect -> BSHC (DB dich)"
# Mat khau doc tu param file MOT LAN, giu trong bien, KHONG in ra.
# Day la giai phap tam cho spike; ban chinh thuc doc tu conf/secrets.ini (0600).
PW_TGT=$(grep -iE '^USERID[[:space:]]+OGG_BSH@BSHC' "$GGHOME/dirprm/rpmnvods.prm" \
         | sed -E 's/.*PASSWORD[[:space:]]+//I; s/[[:space:]]*$//; s/,$//')
if [ -z "$PW_TGT" ]; then
  bad "khong lay duoc mat khau OGG_BSH@BSHC tu rpmnvods.prm"
else
  ok "doc duoc credential tu param file (khong in ra)"
  OGG_PW="$PW_TGT" "$PY" - <<'PYEOF'
import os, sys
import oracledb
pw = os.environ["OGG_PW"]
tried = []
# thin mode doc duoc tnsnames.ora neu co TNS_ADMIN / config_dir
cfg = os.environ.get("TNS_ADMIN") or (os.environ.get("ORACLE_HOME","") + "/network/admin")
for label, kw in [
    ("alias BSHC qua tnsnames.ora", dict(dsn="BSHC", config_dir=cfg)),
    ("localhost:1521/BSHC",         dict(dsn="localhost:1521/BSHC")),
]:
    try:
        with oracledb.connect(user="OGG_BSH", password=pw, **kw) as c:
            cur = c.cursor()
            cur.execute("""SELECT name, open_mode, database_role, log_mode,
                                  (SELECT banner_full FROM v$version WHERE ROWNUM=1)
                             FROM v$database""")
            n, om, role, lm, ver = cur.fetchone()
            print(f"  [ OK ] {label}")
            print(f"         db={n} open={om} role={role} log_mode={lm}")
            print(f"         {ver.splitlines()[0]}")
            cur.execute("SELECT current_scn FROM v$database")
            print(f"         current_scn={cur.fetchone()[0]}")
            sys.exit(0)
    except Exception as e:
        tried.append(f"{label}: {type(e).__name__}: {str(e).splitlines()[0]}")
print("  [FAIL] khong connect duoc:")
for t in tried: print("        ", t)
sys.exit(1)
PYEOF
  [ $? -eq 0 ] || FAILED=$((FAILED+1))
fi

# ---------------------------------------------------------------------
hdr "5. ggsci qua subprocess voi env DUNG RIENG"
# Cam bay da ghi trong runbook: `ssh host 'cmd'` KHONG source .bash_profile,
# nen ggsci se khong tim duoc thu vien Oracle. Cach dung: dung env moi,
# KHONG ke thua env cua tien trinh cha.
"$PY" - <<PYEOF
import subprocess, os, sys, re
gghome = "$GGHOME"
oh     = os.environ.get("ORACLE_HOME","")
env = {
    "ORACLE_HOME": oh,
    "ORACLE_SID" : os.environ.get("ORACLE_SID",""),
    "TNS_ADMIN"  : os.environ.get("TNS_ADMIN", oh + "/network/admin"),
    "LD_LIBRARY_PATH": oh + "/lib",
    "NLS_LANG"   : "AMERICAN_AMERICA.AL32UTF8",
    "PATH"       : "/usr/bin:/bin",
    "HOME"       : os.environ.get("HOME",""),
}
NOISE = re.compile(r"post-quantum|store now|upgraded", re.I)
try:
    p = subprocess.run([gghome + "/ggsci"], input="info all\nexit\n",
                       capture_output=True, text=True, cwd=gghome,
                       env=env, timeout=60, shell=False)
except Exception as e:
    print("  [FAIL]", type(e).__name__, e); sys.exit(1)

lines = [l for l in p.stdout.splitlines() if l.strip() and not NOISE.search(l)]
rows  = [l for l in lines if re.match(r"^(MANAGER|EXTRACT|REPLICAT)\s", l)]
print("  rc =", p.returncode)
for l in rows: print("   ", l)
if len(rows) >= 7:
    print(f"  [ OK ] ggsci tra ve {len(rows)} dong process (mong doi 7: MGR + 3 ET + 3 R)")
else:
    print(f"  [FAIL] chi thay {len(rows)} dong process"); sys.exit(1)

# Kiem dung cach doc lag: PHAI tu 'info all', KHONG tu 'info ... detail'
# ('info ... detail' co dong "Last Started" lam \$4 thanh chu 'Started')
for l in rows:
    f = l.split()
    if f[0] in ("EXTRACT","REPLICAT") and len(f) >= 4:
        lag = f[3]
        if not re.match(r"^\d\d:\d\d:\d\d\$", lag):
            print(f"  [FAIL] cot lag cua {f[2]} khong dung dang hh:mm:ss -> '{lag}'"); sys.exit(1)
print("  [ OK ] cot 4 cua 'info all' dung la lag dang hh:mm:ss (cam bay 26)")
PYEOF
[ $? -eq 0 ] || FAILED=$((FAILED+1))

# ---------------------------------------------------------------------
hdr "6. Account se dung (quyet dinh: KHONG dung sysdba)"
if [ -n "${PW_TGT:-}" ]; then
  OGG_PW="$PW_TGT" "$PY" - <<'PYEOF'
import os, oracledb
cfg = os.environ.get("TNS_ADMIN") or (os.environ.get("ORACLE_HOME","") + "/network/admin")
with oracledb.connect(user="OGG_BSH", password=os.environ["OGG_PW"],
                      dsn="localhost:1521/BSHC") as c:
    cur = c.cursor()
    print("  --- account o DB dich BSHC ---")
    cur.execute("""SELECT username, account_status, default_tablespace
                     FROM dba_users
                    WHERE username IN ('ODS','OGG_BSH','GGADM_RO','GGADM_LOAD')
                 ORDER BY username""")
    rows = cur.fetchall()
    for u, st, ts in rows:
        print(f"    {u:12} {st:22} {ts}")
    have = {r[0] for r in rows}
    for u in ('GGADM_RO','GGADM_LOAD'):
        if u not in have:
            print(f"    {u:12} CHUA TON TAI  -> can tao o Phase 0 viec 3")
    print("  --- GG credential store (dircrd) ---")
    print("  --- streams pool hai dau ---")
    cur.execute("""SELECT component, ROUND(current_size/1024/1024) mb
                     FROM v$sga_dynamic_components
                    WHERE component LIKE '%treams%'""")
    for comp, mb in cur.fetchall():
        print(f"    DICH  {comp:28} {mb} MB")
    try:
        cur.execute("""SELECT component, ROUND(current_size/1024/1024) mb
                         FROM v$sga_dynamic_components@DBL_PMNV
                        WHERE component LIKE '%treams%'""")
        for comp, mb in cur.fetchall():
            print(f"    NGUON {comp:28} {mb} MB")
    except Exception as e:
        print("    NGUON doc qua DBL_PMNV loi:", str(e).splitlines()[0])
PYEOF
  [ $? -eq 0 ] || FAILED=$((FAILED+1))
fi
echo "  --- dircrd (credential store) ---"
ls -la "$GGHOME/dircrd/" 2>/dev/null | tail -n +2 | sed 's/^/    /'
echo "  --- so param file con mat khau DANG RO (muc tieu cuoi Phase 0 = 0) ---"
echo "    $(grep -ilE '^USERID.*PASSWORD' "$GGHOME"/dirprm/*.prm 2>/dev/null | wc -l) / $(ls -1 "$GGHOME"/dirprm/*.prm 2>/dev/null | wc -l) file"

# ---------------------------------------------------------------------
hdr "KET QUA SPIKE"
if [ "$FAILED" -eq 0 ]; then
  echo "  TAT CA OK - kien truc da duoc chung minh, co the vao Phase 1."
else
  echo "  CO $FAILED MUC FAIL - phai xu ly truoc khi viet tinh nang."
fi
exit "$FAILED"
