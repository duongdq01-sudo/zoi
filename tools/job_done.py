#!/usr/bin/env python3
"""Cap nhat trang thai job khi tien trinh job ket thuc.

    job_done.py <job_id> <ma_thoat_sqlplus>

CHINH TIEN TRINH JOB goi file nay, KHONG phai app. Neu de app ghi thi lai
quay ve dung van de ban dau: app chet la job treo RUNNING vinh vien.

Doc duoi log de lay cac moc ma script in ra (`MAXSCN=`, `SO_DONG=`), va neu
that bai thi lay vai dong cuoi lam `err_msg`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ggadmin.db"

# Dong ORA-xxxxx dau tien thuong la nguyen nhan that; cac dong sau la he qua.
RE_ORA = re.compile(r"\b(ORA-\d{5}|SP2-\d{4}|PLS-\d{5})\b")
# Moc do script in ra.
# `SO_DONG_<TEN_BANG>` la moc rieng cua tung bang trong mot LO nhieu bang:
# script nap ca lo in mot dong cho moi bang, de buoc kiem chung doi chieu
# duoc tung bang chu khong chi doi chieu tong. Phan ten bang gioi han theo
# dung quy tac dinh danh Oracle, nen mot dong log bat ky co dau `=` khong
# bien thanh moc.
# `SO_DONG_<BANG>` dat TRUOC `SO_DONG` trong nhom chon: de `SO_DONG` truoc
# thi voi dong `SO_DONG_CN_CH=5` regex khop `SO_DONG` roi moi vap phai `_`,
# phai quay lui moi khop dung. Dat dai truoc ngan la khong phu thuoc vao
# hanh vi quay lui.
RE_MOC = re.compile(
    r"^\s*(SO_DONG_[A-Z][A-Z0-9_$#]{0,29}|MAXSCN|SO_DONG|SO_INDEX|SO_LO)"
    r"\s*=\s*(-?\d+)\s*$", re.M)


def main() -> int:
    if len(sys.argv) != 3:
        print(f"cách dùng: {sys.argv[0]} <job_id> <mã thoát>", file=sys.stderr)
        return 2
    job_id, rc = int(sys.argv[1]), int(sys.argv[2])

    logf = ROOT / "logs" / f"job_{job_id}.log"
    noi_dung = logf.read_text(encoding="utf-8", errors="replace") \
        if logf.exists() else ""

    moc = {m.group(1): int(m.group(2)) for m in RE_MOC.finditer(noi_dung)}
    loi = [l.strip() for l in noi_dung.splitlines() if RE_ORA.search(l)]

    # Ma thoat 0 ma van co ORA- thi KHONG coi la thanh cong: `WHENEVER
    # SQLERROR EXIT FAILURE` co the bi mot khoi EXCEPTION bat mat.
    dat = (rc == 0 and not loi)
    state = "SUCCEEDED" if dat else "FAILED"
    err = None
    if not dat:
        err = (f"rc={rc}; " + "; ".join(loi[:3]))[:400] if loi else \
            f"rc={rc}; xem logs/job_{job_id}.log"

    con = sqlite3.connect(DB, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=20000")
        con.execute(
            "UPDATE job SET state=?, finished_at=?, progress_pct=?,"
            " progress_note=?, result_json=?, err_msg=? WHERE id=?",
            (state, int(time.time()), 100.0 if dat else None,
             f"tien trinh job ket thuc, rc={rc}",
             json.dumps(moc, ensure_ascii=False) if moc else None,
             err, job_id))
        con.commit()
    finally:
        con.close()
    print(f"job #{job_id} -> {state}" + (f" ({err})" if err else "")
          + (f" moc={moc}" if moc else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
