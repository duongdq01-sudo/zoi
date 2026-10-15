#!/bin/bash
# run.sh {start|stop|status|restart|tail} - chay ggadmin tren server.
# nohup + detach: app song qua lan dang xuat SSH. Khong dung systemd vi
# Linger=no nen service --user chet khi logout; cron @reboot moi chac an.
set -u
D=/u01/app/ggadmin
PID=$D/run/ggadmin.pid
LOG=$D/logs/app.log
PY=$D/venv/bin/python3
mkdir -p "$D/run" "$D/logs"
cd "$D" || exit 1

alive() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

case "${1:-status}" in
  start)
    if alive; then echo "dang chay, pid $(cat "$PID")"; exit 0; fi
    # --workers 1 BAT BUOC: nhieu worker se nhan ban poller va tranh ghi SQLite
    nohup "$PY" -m uvicorn app.main:app \
      --host 127.0.0.1 --port 8099 --workers 1 \
      >> "$LOG" 2>&1 &
    echo $! > "$PID"
    sleep 4
    if alive; then echo "da start, pid $(cat "$PID")"; else echo "START THAT BAI"; tail -30 "$LOG"; exit 1; fi
    ;;
  stop)
    if alive; then kill "$(cat "$PID")"; sleep 2; fi
    alive && { kill -9 "$(cat "$PID")" 2>/dev/null; sleep 1; }
    rm -f "$PID"; echo "da stop"
    ;;
  restart)
    # Dung duong dan TUYET DOI: $0 chi la 'run.sh' khi goi 'bash run.sh', ma
    # '.' khong nam trong PATH -> 'command not found', va restart im lang
    # khong lam gi ca. Da gap that 2026-10-13.
    SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
    bash "$SELF" stop; bash "$SELF" start ;;
  status)
    if alive; then
      echo "RUNNING pid $(cat "$PID")  RSS $(ps -o rss= -p "$(cat "$PID")" | tr -d ' ') KB"
      curl -fsS --max-time 5 http://127.0.0.1:8099/healthz || echo "healthz khong tra loi"
    else echo "STOPPED"; fi
    ;;
  tail) tail -${2:-40} "$LOG" ;;
  *) echo "Dung: run.sh {start|stop|restart|status|tail}"; exit 1 ;;
esac
