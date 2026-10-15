#!/usr/bin/env python3
"""Backup nong ggadmin.db + chuoi audit. Chay tu cron.

    backup_db.py [so_ban_giu]        # mac dinh giu 30

VI SAO DUNG Connection.backup() CHU KHONG `cp`
`cp` mot file SQLite dang o che do WAL trong luc app dang ghi se cho ra ban
sao CO THE HONG: WAL nam o file khac (`-wal`) va checkpoint co the dang do
dang. `Connection.backup()` la API backup nong dung cach - no doc tung trang
qua chinh engine SQLite nen ban sao luon nhat quan, va KHONG can dung app.

Server nay khong co `sqlite3` CLI nen khong dung `.backup` cua CLI duoc.

VI SAO BACKUP CA FILE AUDIT JSONL
Chuoi hash audit nam o HAI noi: bang `audit_log` trong DB va
`logs/audit-YYYYMM.jsonl`. Trung lap do la co y - mot he audit ma ke tan
cong xoa duoc bang CUNG dac quyen thi khong phai audit. Backup ma chi lay DB
thi mat han nua y nghia cua viec trung lap.

KHONG backup conf/secrets.ini: nhan ban mat khau ra them mot cho voi bao
dam yeu hon la lam giam an toan, khong phai tang.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ggadmin.db"
BK = ROOT / "backup"
GIU_MAC_DINH = 30


def nen(src: Path, dst_gz: Path) -> int:
    with src.open("rb") as fi, gzip.open(dst_gz, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, length=1024 * 1024)
    os.chmod(dst_gz, 0o600)
    return dst_gz.stat().st_size


def main() -> int:
    giu = int(sys.argv[1]) if len(sys.argv) > 1 else GIU_MAC_DINH
    if not DB.exists():
        print(f"không thấy {DB}", file=sys.stderr)
        return 1
    BK.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(BK, 0o700)
    except OSError:
        pass

    ngay = time.strftime("%Y%m%d")
    tam = BK / f"ggadmin-{ngay}.db.tmp"
    dich = BK / f"ggadmin-{ngay}.db.gz"
    t0 = time.monotonic()

    # Backup nong: khong can dung app, ban sao luon nhat quan.
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(tam)
        try:
            src.backup(dst, pages=512)
        finally:
            dst.close()
    finally:
        src.close()

    # Kiem ban sao DOC DUOC truoc khi coi la thanh cong. Mot ban backup
    # chua bao gio mo thu thi khong phai backup.
    k = sqlite3.connect(f"file:{tam}?mode=ro", uri=True)
    try:
        n_audit = k.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        n_metric = k.execute("SELECT COUNT(*) FROM metric_sample").fetchone()[0]
        toan_ven = k.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        k.close()
    if toan_ven != "ok":
        print(f"kiểm toàn vẹn = {toan_ven!r} -> KHÔNG nhận bản lưu này",
              file=sys.stderr)
        tam.unlink(missing_ok=True)
        return 2

    cl = nen(tam, dich)
    # Xoa ca hai file ben canh: ket noi dich o che do WAL sinh ra `-shm` va
    # `-wal`. Chi xoa file `.tmp` thi hai file do tich lai moi ngay.
    for hau_to in ("", "-shm", "-wal", "-journal"):
        Path(str(tam) + hau_to).unlink(missing_ok=True)
    print(f"  {dich.name}  {cl:,} byte  ({time.monotonic() - t0:.1f}s)  vết thao tác "
          f"{n_audit} bản ghi, số liệu đo {n_metric:,} điểm, kiểm toàn vẹn đạt")

    # Chuoi audit nam o ca file jsonl - backup luon
    n_js = 0
    for f in sorted((ROOT / "logs").glob("audit-*.jsonl")):
        d = BK / f"{f.stem}-{ngay}.jsonl.gz"
        nen(f, d)
        n_js += 1
        print(f"  {d.name}  {d.stat().st_size:,} byte")
    if not n_js:
        print("  (không thấy file audit-*.jsonl)")

    # Don ban cu
    for mau in ("ggadmin-*.db.gz", "audit-*.jsonl.gz"):
        cac = sorted(BK.glob(mau), reverse=True)
        for f in cac[giu:]:
            f.unlink()
            print(f"  đã xoá bản cũ {f.name}")
    print(f"  giữ {giu} bản; hiện có {len(list(BK.glob('ggadmin-*.db.gz')))} bản")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
