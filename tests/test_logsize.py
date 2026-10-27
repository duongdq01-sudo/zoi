"""Test Bieu do log (app/logsize.py). Khong can Oracle/ggsci; dung thu muc
tam va SQLite trong bo nho."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as dbmod                                   # noqa: E402
from app import logsize                                       # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def phat_sinh_khac_tang_rong_khi_co_purge():
    # trail: ghi 100 -> 300, purge ve 50, ghi len 250. Rong = 150, phat sinh = 400.
    d = [(0, 100.0), (3600, 300.0), (7200, 50.0), (10800, 250.0)]
    g = logsize.tang_truong(d)
    assert g["rong"] == 150, g
    assert g["phat_sinh"] == 400, g
    assert abs(g["rong_ngay"] - 150 / (10800 / 86400)) < 1e-6


@test
def duoi_mot_gio_khong_suy_ra_toc_do_ngay():
    g = logsize.tang_truong([(0, 1.0), (600, 2.0)])
    assert g["rong"] == 1 and g["rong_ngay"] is None and g["phat_sinh_ngay"] is None


@test
def mot_diem_thi_khong_co_tang_truong():
    assert logsize.tang_truong([(0, 5.0)])["rong"] is None


@test
def do_doc_binh_phuong_toi_thieu():
    # tang dung 10 byte/ngay, them mot diem lech (don dep) khong lam am do doc
    d = [(i * 86400, 1000.0 + 10 * i) for i in range(10)]
    assert abs(logsize.do_doc(d) - 10) < 1e-9
    d[5] = (d[5][0], d[5][1] - 30)
    assert logsize.do_doc(d) > 0
    assert logsize.do_doc([(0, 1.0), (60, 2.0)]) is None   # duoi 1 gio


@test
def do_nhom_de_quy_khong_dem_trung():
    with tempfile.TemporaryDirectory() as t:
        p = Path(t)
        (p / "a").mkdir()
        (p / "a" / "sub").mkdir()
        (p / "a" / "x.log").write_bytes(b"1" * 100)
        (p / "a" / "sub" / "y.trc").write_bytes(b"1" * 50)
        (p / "z.log").write_bytes(b"1" * 7)
        # glob chong nhau: thu muc a va file trong a -> khong dem 2 lan
        r = logsize.do_nhom([str(p / "a"), str(p / "a" / "*.log"), str(p / "z.log*"),
                             str(p / "khong_co" / "*")])
        assert r["bytes"] == 157 and r["files"] == 3, r
        assert os.path.isdir(r["mount"])


@test
def ngay_con_lai():
    assert logsize._so_ngay_con(100, 10) == 10
    assert logsize._so_ngay_con(100, 0) is None
    assert logsize._so_ngay_con(100, -5) is None
    assert logsize._so_ngay_con(-3, 10) == 0


def _cfg(t: Path):
    return SimpleNamespace(gg_home=t / "ogg",
                           oracle_home=t / "oracle" / "product" / "19" / "dbhome_1",
                           data_dir=t / "ggadmin" / "data",
                           log_dir=t / "ggadmin" / "logs")


@test
def bao_cao_tu_sqlite():
    with tempfile.TemporaryDirectory() as t:
        tp = Path(t)
        cfg = _cfg(tp)
        con = dbmod.init(tp / "x.db")
        now = int(time.time())
        rows = []
        # trail: phinh 1 MB/gio trong 6 gio
        for i in range(7):
            ts = now - (6 - i) * 3600
            rows.append(("LOG", "gg_trail", "bytes", ts, float(i * 1048576)))
            rows.append(("LOG", "gg_trail", "files", ts, 3.0))
            rows.append(("FS", "/", "used_bytes", ts, 100e9 + i * 1e9))
        con.executemany("INSERT INTO metric_sample(scope,scope_key,metric,ts,val)"
                        " VALUES (?,?,?,?,?)", rows)
        moi = {"ts": now,
               "nhom": {"gg_trail": {"bytes": 6 * 1048576, "files": 3, "mount": "/"}},
               "dia": {"/": {"total": 500e9, "used": 106e9, "avail": 394e9}}}
        bc = logsize.bao_cao(con, cfg, 24, moi, kem_diem=True)
        tr = next(n for n in bc["nhom"] if n["key"] == "gg_trail")
        assert tr["rong"] == 6 * 1048576
        assert abs(tr["rong_ngay"] - 24 * 1048576) < 1
        assert len(tr["diem"]) == 7
        assert bc["tong"]["bytes"] == 6 * 1048576
        d = bc["dia"][0]
        assert abs(d["doc_ngay"] - 24e9) < 1e3, d["doc_ngay"]
        # 394 GB trong, nguong chi doc 25 GiB, tang 24 GB/ngay
        assert abs(d["ngay_den_chi_doc"] - (394e9 - 25 * 1024**3) / 24e9) < 1e-3
        # nhom chua co so lieu: 0 byte, khong no
        a = next(n for n in bc["nhom"] if n["key"] == "ora_audit")
        assert a["bytes"] == 0 and a["rong"] is None
        con.close()


@test
def bao_cao_khoang_dai_dung_ban_gom_gio():
    with tempfile.TemporaryDirectory() as t:
        tp = Path(t)
        con = dbmod.init(tp / "x.db")
        now = int(time.time())
        h = now - now % 3600
        con.executemany(
            "INSERT INTO metric_hourly(scope,scope_key,metric,hour_ts,v_min,v_max,v_avg,v_last,n)"
            " VALUES ('LOG','ora_audit','bytes',?,0,0,0,?,1)",
            [(h - (48 - i) * 3600, float(i * 1000)) for i in range(48)])
        con.execute("INSERT INTO metric_sample(scope,scope_key,metric,ts,val)"
                    " VALUES ('LOG','ora_audit','bytes',?,99999)", (now,))
        bc = logsize.bao_cao(con, _cfg(tp), 168, None)
        a = next(n for n in bc["nhom"] if n["key"] == "ora_audit")
        assert a["bytes"] == 99999          # lay diem moi nhat trong DB
        assert a["rong"] == 99999 - 0
        con.close()


@test
def hang_luu_du_cot():
    m = {"ts": 1, "nhom": {"a": {"bytes": 5, "files": 2, "mount": "/"}},
         "dia": {"/": {"total": 10.0, "used": 4.0, "avail": 6.0}}}
    r = logsize.hang_luu(m)
    assert ("LOG", "a", "bytes", 1, 5.0) in r and ("LOGFS", "/", "used_bytes", 1, 4.0) in r


@test
def han_muc_mac_dinh_va_ghi_de_tu_app_toml():
    with tempfile.TemporaryDirectory() as t:
        cfg = _cfg(Path(t))
        tran, ng = logsize.han_muc(cfg)
        assert ng == 50 and tran["gg_trail"] == 5 * 1024**3
        cfg.log_tran = {"nguong_pct": 70, "gg_trail": 2}
        tran, ng = logsize.han_muc(cfg)
        assert ng == 70 and tran["gg_trail"] == 2 * 1024**3
        assert tran["ora_audit"] == 1 * 1024**3        # nhom khong ghi de giu mac dinh


@test
def vuot_nguong_50_phan_tram_han_muc():
    with tempfile.TemporaryDirectory() as t:
        tp = Path(t)
        con = dbmod.init(tp / "x.db")
        G = 1024**3
        moi = {"ts": int(time.time()),
               # audit han muc 1 GB: 0,6 GB -> vuot; trail han muc 5 GB: 2,5 GB = dung 50% -> chua vuot
               "nhom": {"ora_audit": {"bytes": 0.6 * G, "files": 1, "mount": "/"},
                        "gg_trail": {"bytes": 2.5 * G, "files": 1, "mount": "/"}},
               "dia": {"/": {"total": 500e9, "used": 100e9, "avail": 400e9}}}
        bc = logsize.bao_cao(con, _cfg(tp), 24, moi)
        n = {x["key"]: x for x in bc["nhom"]}
        assert n["ora_audit"]["vuot"] and abs(n["ora_audit"]["pct_tran"] - 60) < 1e-9
        assert not n["gg_trail"]["vuot"]
        assert bc["so_vuot"] == 1 and bc["nguong_pct"] == 50
        con.close()


@test
def toc_do_thuong_bo_qua_ngay_dot_bien():
    # 23/10 that: dia tang 49 GB trong mot ngay, ngay thuong 0,3 GB
    G = 1024**3
    t0 = int(time.mktime((2026, 10, 19, 12, 0, 0, 0, 0, -1)))
    tang = [0.3, 0.3, 0.3, 49, 0.3, 0.3, 0.3]
    v, diem = 200 * G, [(t0, 200 * G)]
    for i, x in enumerate(tang):
        v += x * G
        diem.append((t0 + (i + 1) * 86400, v))
    med, dot = logsize.toc_do_thuong(diem)
    assert abs(med - 0.3 * G) < 1, med
    assert len(dot) == 1 and abs(dot[0][1] - 49 * G) < 1
    assert logsize.do_doc(diem) > 5 * G            # duong thang bi keo lech


@test
def moi_nhom_deu_co_danh_gia_xoa():
    with tempfile.TemporaryDirectory() as t:
        tp = Path(t)
        keys = {g["key"] for g in logsize.nhom_log(_cfg(tp))}
        assert keys == set(logsize.XOA), keys ^ set(logsize.XOA)
        assert all(m in ("duoc", "dieu_kien", "khong") and gc for m, gc in logsize.XOA.values())
        con = dbmod.init(tp / "x.db")
        bc = logsize.bao_cao(con, _cfg(tp), 24, None)
        n = {x["key"]: x for x in bc["nhom"]}
        assert n["gg_trail"]["xoa"] == "khong" and n["ora_audit"]["xoa"] == "duoc"
        con.close()
