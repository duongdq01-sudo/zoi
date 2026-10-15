#!/bin/bash
# deploy.sh - day code len server. Chay tu may Windows (Git Bash).
# Khong day: wheelhouse, vendor-runtime, conf/secrets.ini (da co tren server).
set -euo pipefail
cd "$(dirname "$0")/.."
H=${1:-oracle@172.16.33.90}
D=/u01/app/ggadmin

bash tools/lint_sql.sh >/dev/null || { echo "lint_sql.sh FAIL -> dung"; exit 1; }

# Compile MOI file .py truoc khi day.
# Phai dung compile(), KHONG dung ast.parse(): `await` trong mot ham sync thi
# ast.parse() cho qua ma compile() moi bao loi. Da mac that 2026-10-14 - app
# day len roi khong start duoc, 13 endpoint tra 000.
python - <<'PYC' || { echo "compile FAIL -> dung, khong day"; exit 1; }
import io, pathlib, sys
bad = 0
for f in sorted(pathlib.Path("app").rglob("*.py")):
    try:
        compile(io.open(f, encoding="utf-8").read(), str(f), "exec")
    except SyntaxError as e:
        bad += 1
        print(f"  HONG {f}:{e.lineno} {e.msg}")
sys.exit(1 if bad else 0)
PYC
echo "  compile: $(find app -name '*.py' | wc -l) file OK"

# Test phai sach truoc khi day
python tests/run_all.py >/dev/null 2>&1 || { echo "tests/run_all.py FAIL -> dung"; exit 1; }
echo "  test: sach"
find app web -name '*.py' -o -name '*.html' -o -name '*.css' -o -name '*.js' | while read -r f; do
  sed -i 's/\r$//' "$f" 2>/dev/null || true
done
ssh "$H" "mkdir -p $D/app/adapters $D/app/routers $D/web/templates $D/web/static/vendor $D/tests/fixtures $D/tools"
scp -q -r app/*.py                  "$H:$D/app/"
scp -q -r app/adapters/*.py         "$H:$D/app/adapters/"
scp -q    app/routers/__init__.py   "$H:$D/app/routers/" 2>/dev/null || true
scp -q -r web/templates/*.html      "$H:$D/web/templates/"
scp -q    web/static/style.css      "$H:$D/web/static/"
# tools/: job_connect.py + job_done.py duoc CHINH TIEN TRINH JOB goi, nen
# chung PHAI co tren server; backup_db.py do cron goi. Thieu mot file la job
# chay xong ma khong ai cap nhat trang thai.
scp -q    tools/*.py                "$H:$D/tools/" 2>/dev/null || true
scp -q    tools/*.sh tools/*.sql    "$H:$D/tools/" 2>/dev/null || true
ssh "$H" "chmod 700 $D/tools/*.py $D/tools/*.sh 2>/dev/null || true"
# Test di kem code: mot bo test chi chay duoc tren may dev thi se khong ai
# chay sau khi deploy. Khong can pytest, khong can Oracle/ggsci/SQLite.
scp -q    tests/*.py                "$H:$D/tests/"
scp -q    tests/fixtures/*          "$H:$D/tests/fixtures/"
scp -q -r web/static/vendor/*       "$H:$D/web/static/vendor/"
# KHONG day conf/app.toml theo mac dinh.
# Ly do (loi da gap that 2026-10-13): chat_ids cua group Zalo duoc dat TREN
# SERVER, ban local van rong; mot lan deploy da day ban rong len va lam
# kenh zalo_bot IM LANG TAT. Cau hinh tren server la nguon su that.
if [ "${2:-}" = "--with-config" ] || [ "${1:-}" = "--with-config" ]; then
  echo "  --with-config: dang day conf/app.toml (se GHI DE ban tren server)"
  ssh "$H" "cp -p $D/conf/app.toml $D/conf/app.toml.bak_\$(date +%Y%m%d_%H%M%S)"
  scp -q conf/app.toml "$H:$D/conf/"
else
  echo "  bo qua conf/app.toml (them --with-config neu that su muon day)"
fi
# Kiem CU PHAP JINJA cua template - chay TREN SERVER vi jinja2 chi co trong
# venv o day, may dev khong co.
#
# Vi sao can cong nay: mot template vo cu phap KHONG lam app chet. App van
# start, `/healthz` van 200, compile Python van sach - no chi vo khi nguoi
# dung mo dung trang do. Da gap that: mot ban dich chen thua `}}` lam vo
# bieu thuc dieu kien o `_dashboard_body.html`, chuoi Jinja hien ra nhu van
# ban tren trang Tong quan va se tra 500 khi gia tri la None.
#
# Dat SAU khi scp: phai kiem dung file VUA DAY LEN, khong phai ban cu.
if ! ssh "$H" "cd $D && ./venv/bin/python3 tools/lint_tpl.py"; then
  echo "lint_tpl FAIL -> template vua day len CO LOI. Sua roi day lai." >&2
  exit 1
fi
echo "da day code len $H:$D"
