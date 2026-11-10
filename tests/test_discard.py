"""Phan loai khoi loi trong file discard (DDL/DML) + moc "da xu ly".

Ca goc: 2026-11-10 RHT.dsc co MOT lenh `COMMENT ON COLUMN` bi
`DDLERROR 942 DISCARD` bo. Dashboard bao "5 dong la - CO DU LIEU BI BO" muc
CRIT, trong khi khong mat dong du lieu nao.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db                                              # noqa: E402
from app import discard as D                                    # noqa: E402
from app.adapters import osfs                                   # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


BANNER = ("Oracle GoldenGate Delivery for Oracle process started, group RHT "
          "discard file opened: 2026-10-30 13:24:53.901488\n")

# Noi dung THAT cua RHT.dsc 2026-11-10 (467 byte)
KHOI_DDL = (
    "Current time: 2026-11-10 13:09:46\n"
    " Error text [Error code [942], ORA-00942: table or view does not exist\n"
    "], operation [COMMENT ON COLUMN KT_BSH.HT_NSD_PVI.CB IS\n"
    "            'Ma can bo (HT_MA_NSD.MA trong don vi DVI). Co gia tri = chi xem "
    "don do can bo nay cap; null = ca phong / ca don vi nhu cu' (size 177)]\n"
    "Operation failed at seqno 14 rba 18,268,362.\n")

KHOI_DML = (
    "Current time: 2026-11-10 14:00:00\n"
    "Discarded record from action DISCARD on error 1403\n"
    "OCI Error ORA-01403: no data found, SQL <UPDATE \"ODS\".\"HT_NSD_PVI\" ...>\n"
    "Problem replicating KT_BSH.HT_NSD_PVI to ODS.HT_NSD_PVI\n")


class FakeCfg:
    def __init__(self, root):
        self.root = Path(root)
        self.data_dir = self.root / "data"
        self.discard_baseline = {"RHT": 113}
        self.streams = {"HT": {"extract": "ETHT", "replicat": "RHT", "trail": "ht"}}

    def discard_path(self, rep):
        return self.root / f"{rep}.dsc"


def _ghi(td, noi_dung, mode="w"):
    with (Path(td) / "RHT.dsc").open(mode, encoding="utf-8", newline="") as fh:
        fh.write(noi_dung)


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    return con


@test
def comment_on_column_bi_bo_la_DDL_khong_phai_mat_du_lieu():
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        _ghi(td, BANNER + KHOI_DDL)
        d = osfs.discard_info(cfg, "RHT")
        assert d["so_dong_la"] == 5, d          # van dem dong nhu cu
        assert d["so_lenh_ddl"] == 1, d
        assert d["so_ban_ghi_dml"] == 0, d       # -> KHONG con CRIT


@test
def ban_ghi_1403_la_DML():
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        _ghi(td, BANNER + KHOI_DDL + KHOI_DML)
        d = osfs.discard_info(cfg, "RHT")
        assert d["so_lenh_ddl"] == 1 and d["so_ban_ghi_dml"] == 1, d
        k = osfs.discard_khoi(cfg, "RHT")
        assert [x["loai"] for x in k] == ["DDL", "DML"], k


@test
def dong_la_khong_nhan_ra_thi_coi_la_DML():
    """Khong chac -> coi la nang. Khong bao gio ha cap mot dong la xuong DDL."""
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        _ghi(td, BANNER + "mot dong gi do GG moi ghi\n")
        d = osfs.discard_info(cfg, "RHT")
        assert d["so_ban_ghi_dml"] == 1 and d["so_lenh_ddl"] == 0, d


@test
def khoi_co_ca_operation_DDL_va_dau_hieu_DML_thi_la_DML():
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        _ghi(td, BANNER + "Current time: x\nDiscarded record ... operation [ALTER x]\n")
        d = osfs.discard_info(cfg, "RHT")
        assert d["so_ban_ghi_dml"] == 1, d


@test
def danh_dau_da_xu_ly_thi_het_dem_nhung_khoi_moi_van_bao():
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        con = _con()
        _ghi(td, BANNER + KHOI_DDL)
        xt = D.xem_truoc(cfg, con, "rht")
        assert xt["rep"] == "RHT" and xt["so_ddl"] == 1, xt
        r = D.danh_dau(cfg, con, "RHT", offset=xt["offset"], dau=xt["dau"],
                       who="t", reason="da kiem cot hai ben khop", ddl=1)
        assert Path(r["ban_luu"]).read_text(encoding="utf-8") == BANNER + KHOI_DDL
        acks = db.discard_acks(con)
        d = osfs.discard_info(cfg, "RHT", acks["RHT"])
        assert d["so_dong_la"] == 0 and d["da_xu_ly"] == 1, d
        # File GoldenGate KHONG bi dong vao
        assert (Path(td) / "RHT.dsc").read_text(encoding="utf-8") == BANNER + KHOI_DDL
        # Banner stop sau moc: van sach
        _ghi(td, "Oracle GoldenGate Delivery for Oracle process stopped, group RHT : x\n", "a")
        assert osfs.discard_info(cfg, "RHT", acks["RHT"])["so_dong_la"] == 0
        # Ban ghi moi sau moc: PHAI bao
        _ghi(td, KHOI_DML, "a")
        d = osfs.discard_info(cfg, "RHT", acks["RHT"])
        assert d["so_ban_ghi_dml"] == 1 and d["so_lenh_ddl"] == 0, d


@test
def file_tao_moi_thi_moc_cu_het_hieu_luc():
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        con = _con()
        _ghi(td, BANNER + KHOI_DDL)
        xt = D.xem_truoc(cfg, con, "RHT")
        D.danh_dau(cfg, con, "RHT", offset=xt["offset"], dau=xt["dau"],
                   who="t", reason="x" * 12)
        # Restart voi PURGE: file moi, banner moi, co loi moi ngan hon offset cu
        _ghi(td, BANNER.replace("13:24:53", "15:00:00") + "Current time: y\nla\n")
        d = osfs.discard_info(cfg, "RHT", db.discard_acks(con)["RHT"])
        assert d["da_xu_ly"] == 0 and d["so_ban_ghi_dml"] == 1, d


@test
def danh_dau_tu_choi_khi_file_doi_giua_xem_truoc_va_bam():
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        con = _con()
        _ghi(td, BANNER + KHOI_DDL)
        xt = D.xem_truoc(cfg, con, "RHT")
        _ghi(td, BANNER.replace("13:24:53", "15:00:00"))
        try:
            D.danh_dau(cfg, con, "RHT", offset=xt["offset"], dau=xt["dau"],
                       who="t", reason="x" * 12)
        except D.DiscardError:
            pass
        else:
            raise AssertionError("phai tu choi")
        assert not db.discard_acks(con)


@test
def bo_danh_dau_thi_dem_lai_tu_dau():
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        con = _con()
        _ghi(td, BANNER + KHOI_DDL)
        xt = D.xem_truoc(cfg, con, "RHT")
        D.danh_dau(cfg, con, "RHT", offset=xt["offset"], dau=xt["dau"],
                   who="t", reason="x" * 12)
        D.bo_danh_dau(cfg, con, "RHT")
        d = osfs.discard_info(cfg, "RHT", db.discard_acks(con).get("RHT"))
        assert d["so_lenh_ddl"] == 1, d


@test
def replicat_ngoai_cau_hinh_bi_tu_choi():
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        try:
            D.kiem_replicat(cfg, "../../etc/passwd")
        except D.DiscardError:
            return
        raise AssertionError("phai tu choi")
