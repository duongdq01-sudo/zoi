"""Job chay NGOAI tien trinh web.

VI SAO PHAI DETACH - khong phai ly thuyet, da vo that 2026-10-14

Ban dau toi chay job verify bang `asyncio.create_task` BEN TRONG tien trinh
web. Khi can restart app, task chet theo - va vi nguoi quan ly vong doi task
la chinh tien trinh do, khoi `finally` KHONG chay. Ket qua: job #2 chet giua
luc quiesce va de lai Extract `ETHT` STOPPED; phai vao ggsci start tay.

Ba thu detach cho lai, dung nhu ke hoach ghi:
  1. tunnel dut  -> job van chay
  2. restart app -> job van chay
  3. DBA van `tail -f logs/job_<id>.log` duoc bang tay, dung cai ho quen

CACH DETACH
`subprocess.Popen(..., start_new_session=True)` dat con vao SESSION MOI, nen
no khong nhan tin hieu gui cho process group cua uvicorn. `run.sh restart`
chi kill pid cua uvicorn, khong cham tien trinh job.

MAT KHAU KHONG DI QUA ARGV
`sqlplus user/pw@dsn` de mat khau lo ra trong `ps` cho MOI user tren may.
O day: `tools/job_connect.py` doc conf/secrets.ini (0600) roi in cau
`connect` ra STDOUT, pipe vao `sqlplus -S /nolog`. Mat khau chi ton tai
trong mot pipe, khong vao argv, khong vao file moi.

AI CAP NHAT TRANG THAI JOB
Chinh tien trinh job: buoc cuoi cua wrapper goi `tools/job_done.py` de ghi
vao SQLite. Neu de app ghi thi lai quay ve dung van de ban dau - app chet la
job treo RUNNING vinh vien.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("ggadmin.jobs")

ROOT = Path(__file__).resolve().parent.parent


def _wrapper(cfg, job_id: int, cred: str, sql_path: Path) -> Path:
    """Sinh wrapper bash cho mot job. Luu xuong scripts/ de DBA doc duoc."""
    env = cfg.subprocess_env()
    xuat = "\n".join(f'export {k}={v!r}' for k, v in sorted(env.items()))
    sqlplus = cfg.oracle_home / "bin" / "sqlplus"
    logf = cfg.log_dir / f"job_{job_id}.log"
    body = f'''#!/bin/bash
# ggadmin - viec chay nen #{job_id}, chay NGOAI tien trinh web.
#
# Doc duoc, chay tay duoc: `bash {cfg.scripts_dir}/job_{job_id}.sh`
# Theo doi:                `tail -f {logf}`
#
# Bien moi truong dung rieng, KHONG ke thua tu tien trinh cha: `ssh host
# 'cmd'` khong doc .bash_profile, va tien trinh web co y chay voi
# LD_LIBRARY_PATH sach.
set -u
{xuat}

LOG={str(logf)!r}
exec >> "$LOG" 2>&1
echo "=== viec #{job_id} bat dau $(date '+%F %T') pid $$ ==="

# Mat khau di qua PIPE, khong qua dong lenh - `ps` khong thay duoc.
{str(ROOT / "venv" / "bin" / "python3")!r} {str(ROOT / "tools" / "job_connect.py")!r} \\
    {cred!r} {str(sql_path)!r} | {str(sqlplus)!r} -S /nolog
RC=${{PIPESTATUS[1]}}

echo "=== viec #{job_id} xong $(date '+%F %T') ma thoat=$RC ==="
# Chinh tien trinh nay cap nhat trang thai, KHONG phai tien trinh web.
{str(ROOT / "venv" / "bin" / "python3")!r} {str(ROOT / "tools" / "job_done.py")!r} \\
    {job_id} "$RC"
'''
    cfg.scripts_dir.mkdir(parents=True, exist_ok=True)
    p = cfg.scripts_dir / f"job_{job_id}.sh"
    p.write_text(body, encoding="utf-8")
    os.chmod(p, 0o700)
    return p


def spawn_sqlplus(cfg, con, *, kind: str, sql_path: Path, cred: str,
                  created_by: str, params: dict | None = None,
                  mot_job_mot_lan: bool = True) -> int:
    """Tao job va spawn tien trinh ROI RA. Tra job_id ngay.

    `mot_job_mot_lan`: toi da MOT job cung loai tren toan he. Ly do:
    RAM available chi ~6 GB; de hai dot nap chay song song thi chinh cong cu
    gay ra su co no dang giam sat.
    """
    if cred not in cfg.creds:
        raise RuntimeError(
            f"Thiếu khối [{cred}] trong conf/secrets.ini. Thêm khối đó (quyền 0600) "
            "rồi thử lại; hoặc chạy script bằng tay.")
    if not Path(sql_path).exists():
        raise RuntimeError(f"Không thấy script {sql_path}")

    if mot_job_mot_lan:
        r = con.execute(
            "SELECT id, kind FROM job WHERE state IN ('QUEUED','RUNNING')"
        ).fetchone()
        if r:
            raise RuntimeError(
                f"Đang có việc #{r['id']} ({r['kind']}) chạy - tối đa 1 việc trên toàn "
                "hệ. Chờ nó xong hoặc xem trang Việc chạy.")

    cur = con.execute(
        "INSERT INTO job(kind,state,created_at,created_by,params_json,"
        "progress_note) VALUES (?,'QUEUED',?,?,?,?)",
        (kind, int(time.time()), created_by,
         json.dumps(dict(params or {}, sql=str(sql_path), cred=cred),
                    ensure_ascii=False),
         "đang tạo tiến trình chạy rời ra"))
    job_id = int(cur.lastrowid)

    wrap = _wrapper(cfg, job_id, cred, Path(sql_path))
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.Popen(
            ["/bin/bash", str(wrap)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(ROOT),
            start_new_session=True,     # <- day moi la phan "detach"
            env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/oracle")},
        )
    except OSError as e:
        con.execute("UPDATE job SET state='FAILED', finished_at=?, err_msg=? "
                    "WHERE id=?", (int(time.time()), f"không tạo được tiến trình: {e}",
                                   job_id))
        raise RuntimeError(f"Không tạo được việc chạy nền: {e}") from e

    # Ghi `detach` + `pid` vao params_json. Khong phai de trang tri:
    # `_reconcile_jobs()` luc app khoi dong dung hai truong nay de biet job
    # nao CON SONG. Thieu chung thi job detach dang chay bi danh dau KILLED
    # oan - da xay ra that trong phien xay dung, va hau qua that la UI bao
    # KILLED suot mot gio trong khi dot nap van chay, dong thoi pre-flight
    # `khong_co_job_khac` cho phep mot dot nap thu hai khoi dong.
    con.execute(
        "UPDATE job SET state='RUNNING', started_at=?, progress_note=?,"
        " params_json=? WHERE id=?",
        (int(time.time()),
         f"pid {p.pid}, log: logs/job_{job_id}.log",
         json.dumps(dict(params or {}, sql=str(sql_path), cred=cred,
                         detach=True, pid=p.pid), ensure_ascii=False),
         job_id))
    log.info("việc #%s (%s) đã chạy rời ra, pid %s, script bọc %s",
             job_id, kind, p.pid, wrap)
    return job_id


def con_song(job: dict) -> bool:
    """Job detach nay con tien trinh dang chay khong?

    Kiem HAI dieu, khong chi mot: pid con ton tai VA cmdline cua no con chua
    `job_<id>.sh`. Chi kiem pid thi gap pid bi dung lai (PID reuse) se cho
    ket luan sai - tren may co 16 vCPU va nhieu tien trinh Oracle thi pid
    quay vong khong phai chuyen hiem.
    """
    try:
        p = json.loads(job.get("params_json") or "{}")
    except (TypeError, ValueError):
        return False
    if not p.get("detach") or not p.get("pid"):
        return False
    pid = int(p["pid"])
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    return f"job_{job['id']}.sh" in cmd


def trang_thai(con, job_id: int) -> dict | None:
    r = con.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def dang_chay(con) -> dict | None:
    r = con.execute(
        "SELECT * FROM job WHERE state IN ('QUEUED','RUNNING')"
        " ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def duoi_log(cfg, job_id: int, n: int = 60) -> list[str]:
    p = cfg.log_dir / f"job_{job_id}.log"
    if not p.exists():
        return []
    from .adapters.osfs import _tail, redact
    return [redact(l) for l in _tail(p, n)]
