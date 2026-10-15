"""Lich su so lieu: truy van chuoi thoi gian, rollup theo gio, purge.

metric_sample la long/narrow + WITHOUT ROWID voi PK
(scope, scope_key, metric, ts) -> "lag cua RPMNVODS 7 ngay qua" la mot
range scan lien mach, dung pattern ma do thi can.

Giu 90 ngay raw roi purge; rollup theo gio giu mai -> file duoi 200 MB
vinh vien. Truy van tu dong chon raw hay rollup theo khoang thoi gian.
"""

from __future__ import annotations

import logging
import sqlite3
import time

log = logging.getLogger("ggadmin.history")

# Cac chuoi san co cho trang lich su: (nhan, scope, metric, don vi, he so)
SERIES = {
    "lag": ("Độ chậm của các tiến trình", "PROCESS", "lag_s", "giay", 1.0),
    "trail": ("Dung lượng trail", "TRAIL", "total_bytes", "MB", 1 / 1048576),
    "discard": ("Số dòng dữ liệu bị bỏ", "DISCARD", "delta_bytes", "byte", 1.0),
    "room": ("Room tablespace", "TABLESPACE", "room_mb", "MB", 1.0),
    "datafile": ("Dung lượng datafile", "DATAFILE", "size_mb", "MB", 1.0),
    "disk": ("Đĩa / còn trống", "FS", "avail_gb", "GB", 1.0),
}


def series(con: sqlite3.Connection, kind: str, hours: int = 24
           ) -> dict:
    """Tra ve {nhan, don_vi, nguon, keys:[...], diem:{key: [[ts_ms, val], ...]}}

    Tu dong dung rollup khi khoang thoi gian dai: raw 30 giay/diem thi 30
    ngay la ~86.000 diem/chuoi, ve khong kip va khong can thiet.
    """
    if kind not in SERIES:
        raise ValueError(f"loại chuỗi số liệu không hợp lệ: {kind}")
    label, scope, metric, unit, mul = SERIES[kind]
    now = int(time.time())
    since = now - hours * 3600
    dung_rollup = hours > 72

    if dung_rollup:
        rows = con.execute(
            "SELECT scope_key, hour_ts ts, v_max val FROM metric_hourly"
            " WHERE scope=? AND metric=? AND hour_ts >= ?"
            " ORDER BY scope_key, hour_ts", (scope, metric, since)).fetchall()
        nguon = "bản gom theo giờ (giá trị đỉnh)"
    else:
        rows = con.execute(
            "SELECT scope_key, ts, val FROM metric_sample"
            " WHERE scope=? AND metric=? AND ts >= ?"
            " ORDER BY scope_key, ts", (scope, metric, since)).fetchall()
        nguon = "số liệu gốc, 30 giây một điểm"

    diem: dict[str, list[list[float]]] = {}
    for r in rows:
        diem.setdefault(r["scope_key"], []).append(
            [r["ts"] * 1000, round(r["val"] * mul, 3)])
    return {"nhan": label, "don_vi": unit, "nguon": nguon,
            "keys": sorted(diem), "diem": diem,
            "tu": since * 1000, "den": now * 1000}


def rollup(con: sqlite3.Connection, upto: int | None = None) -> int:
    """Gop metric_sample thanh metric_hourly. Idempotent.

    Chi gop cac gio DA XONG (khong gop gio hien tai, vi no con nhan diem).
    """
    now = upto or int(time.time())
    hien_tai = now - (now % 3600)
    cur = con.execute("""
        INSERT INTO metric_hourly(scope,scope_key,metric,hour_ts,
                                  v_min,v_max,v_avg,v_last,n)
        SELECT scope, scope_key, metric, (ts / 3600) * 3600 h,
               MIN(val), MAX(val), AVG(val),
               (SELECT val FROM metric_sample x
                 WHERE x.scope=m.scope AND x.scope_key=m.scope_key
                   AND x.metric=m.metric AND (x.ts/3600)*3600 = (m.ts/3600)*3600
                 ORDER BY x.ts DESC LIMIT 1),
               COUNT(*)
          FROM metric_sample m
         WHERE ts < ?
         GROUP BY scope, scope_key, metric, h
        ON CONFLICT(scope,scope_key,metric,hour_ts) DO UPDATE SET
          v_min=excluded.v_min, v_max=excluded.v_max, v_avg=excluded.v_avg,
          v_last=excluded.v_last, n=excluded.n
    """, (hien_tai,))
    return cur.rowcount or 0


def purge(con: sqlite3.Connection, retain_days: int) -> int:
    """Xoa raw cu hon retain_days. CHI xoa phan DA rollup.

    Thu tu nay quan trong: purge truoc rollup se mat du lieu vinh vien.
    """
    cutoff = int(time.time()) - retain_days * 86400
    cutoff = cutoff - (cutoff % 3600)
    con.execute("DELETE FROM metric_sample WHERE ts < ? AND (ts/3600)*3600 IN"
                " (SELECT hour_ts FROM metric_hourly)", (cutoff,))
    n1 = con.total_changes
    return n1


def stats(con: sqlite3.Connection) -> dict:
    r = con.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT ts) t, MIN(ts) a, MAX(ts) b"
        " FROM metric_sample").fetchone()
    h = con.execute(
        "SELECT COUNT(*) n, MIN(hour_ts) a, MAX(hour_ts) b"
        " FROM metric_hourly").fetchone()
    return {
        "raw_diem": r["n"], "raw_tick": r["t"],
        "raw_tu": r["a"], "raw_den": r["b"],
        "gio_diem": h["n"], "gio_tu": h["a"], "gio_den": h["b"],
    }
