#!/bin/bash
# trien_khai.sh - day code len server, khoi dong lai, roi KIEM CHUNG.
#
# Chay tu may Windows (Git Bash):   bash tools/trien_khai.sh
#
# VI SAO CO FILE NAY, khong goi thang deploy.sh:
#   1. GO MAT KHAU MOT LAN. `deploy.sh` mo khoang 10 ket noi ssh/scp rieng
#      le, moi cai hoi mat khau mot lan. O day bat ControlMaster truoc, moi
#      ket noi sau di chung mot duong - go mot lan cho ca luot.
#   2. BACKUP DB TRUOC khi khoi dong lai. Ban 2026-10-15 co mot migration
#      dung lai bang `wizard_run` (them LUONG_MOI vao rang buoc CHECK).
#      Migration tu luu `.premigrate`, nhung mot ban backup day du truoc do
#      van la thu nen co.
#   3. KIEM CHUNG SAU KHI CHAY, khong tin vao "da day xong": doc lai
#      `.premigrate`, doc log, va goi /healthz.
set -u
H=${1:-oracle@172.16.33.90}
D=/u01/app/ggadmin
cd "$(dirname "$0")/.." || exit 1

CM=~/.ssh/cm-trienkhai-$$
SSH="ssh -o ControlMaster=auto -o ControlPath=$CM -o ControlPersist=600"
don() { $SSH -O exit "$H" 2>/dev/null; rm -f "$CM"; }
trap don EXIT

echo "=== 1/5  Mo ket noi dung chung (go mat khau MOT lan) ==="
$SSH "$H" "echo ket noi OK: \$(hostname)" || { echo "khong ket noi duoc"; exit 1; }

echo
echo "=== 2/5  Backup DB truoc khi doi lược đồ ==="
$SSH "$H" "cd $D && ./venv/bin/python3 tools/backup_db.py" || {
  echo "BACKUP KHONG XONG -> dung lai, khong day code."; exit 1; }

echo
echo "=== 3/5  Day code (deploy.sh tu chay lint + compile + test truoc) ==="
# deploy.sh doc hai bien SSH/SCP (mac dinh la `ssh`/`scp`) - truyen ban co
# ControlPath vao de ca 10 ket noi cua no di chung mot duong da mo o tren.
SSH="$SSH" SCP="scp -o ControlMaster=auto -o ControlPath=$CM -o ControlPersist=600"   bash tools/deploy.sh "$H" || {
  echo "DEPLOY KHONG XONG -> khong khoi dong lai."; exit 1; }

echo
echo "=== 4/5  Khoi dong lai ==="
$SSH "$H" "bash $D/tools/run.sh restart" || { echo "RESTART HONG"; exit 1; }

echo
echo "=== 5/5  Kiem chung ==="
$SSH "$H" "
  echo '--- trang thai ---'
  bash $D/tools/run.sh status
  echo '--- ban DB truoc nang cap (neu migration da chay) ---'
  ls -la $D/data/*.premigrate 2>/dev/null || echo '(khong co - lược đồ da moi san)'
  echo '--- rang buoc kind cua wizard_run ---'
  ./venv/bin/python3 - <<'PYC'
import sqlite3
con = sqlite3.connect('$D/data/ggadmin.db')
sql = con.execute(\"SELECT sql FROM sqlite_master WHERE name='wizard_run'\").fetchone()[0]
print('LUONG_MOI trong CHECK:', 'LUONG_MOI' in sql)
print('so phien hien co     :', con.execute('SELECT COUNT(*) FROM wizard_run').fetchone()[0])
PYC
  echo '--- 5 dong log cuoi ve nang cap ---'
  grep -i 'nang cap\|migration\|LUONG_MOI' $D/logs/app.log | tail -5 || true
"

echo
echo "--- kiem tu phia may nay (qua duong ham, neu dang mo) ---"
curl -s -m 8 http://127.0.0.1:8099/healthz || echo "(duong ham chua mo - chay: ssh -N gg-admin)"
echo
echo "XONG. Viec tiep theo KHONG tu dong: dien tap don sach truoc lan chay that."
