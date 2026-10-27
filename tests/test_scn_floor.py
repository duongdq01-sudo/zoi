"""Test buoc SCN_FLOOR khi Replicat CHUA TUNG AP giao dich nao.

Phien #21 (2026-10-23, lo 1 PMNV3 - 20 bang): buoc 13 FAIL "Khong doc duoc
moc High Watermark". RPMNV3 vua tao o phien #20 voi bang dau BH_XE_LOAI khong
co DML nao -> Replicat van o ban ghi dau cua pp000000000 (RBA 1342) va DB
khong in moc nao (khong high_wm, low_wm, log_bsn). So lieu trong file nay la
so lieu THAT doc tu server luc 11:39 hom do.

Khong can Oracle, khong can ggsci: gg.detail / gg.run deu gia.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as dbmod                                   # noqa: E402
from app import wizard as wz                                  # noqa: E402
from app.adapters import gg as ggmod                          # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# `send extract ETPMNV3, status` that, 2026-10-23 11:39
STATUS_XONG = """Sending STATUS request to Extract group ETPMNV3 ...
EXTRACT ETPMNV3 (PID 2006022)
  Current status: Recovery complete: Processing data
  Current read position:
  Redo thread #: 1
  Sequence #: 133552
  RBA: 55448
  Timestamp: 2026-10-23 11:39:35.000000
  SCN: 0.494765488 (494765488)
  Current write position:
  Sequence #: 1
  RBA: 1404
  Timestamp: 2026-10-23 11:39:37.429727
  Extract Trail: /u01/app/ogg/dirdat/pp"""
STATUS_RECOVERY = STATUS_XONG.replace(
    "Recovery complete: Processing data", "In recovery[1]: Processing data")
# gg.detail RPMNV3 that: chi co vi tri doc, khong moc nao
REP_VANG = {"read_file": "/u01/app/ogg/dirdat/pp000000000", "rba": "1342"}


class _Cfg:
    streams = {"PMNV3": {"extract": "ETPMNV3", "replicat": "RPMNV3", "trail": "pp"}}

    def __init__(self, tmp: Path):
        self.gg_home = tmp


def _phien():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(dbmod.SCHEMA)
    con.execute(
        "INSERT INTO wizard_run(kind,state,src_owner,src_table,tgt_owner,tgt_table,"
        "stream,created_at,created_by,updated_at,cur_step,ctx_json)"
        " VALUES ('ADD_TABLE','OPEN','KT_BSH','B','ODS','B','PMNV3',1,'t',1,13,'{}')")
    w = wz.Wizard(_Cfg(Path(tempfile.mkdtemp())), con, None, None, None)
    return w, w.lay(1)


def _chay(rep: dict, status: str | None):
    """Chay _b_scn_floor voi detail/run gia. Tra (ket qua|loi, wizard, so lan goi run)."""
    goc = (ggmod.detail, ggmod.run)
    goi: list[str] = []

    async def detail(cfg, proc):
        assert proc == "RPMNV3", proc
        return dict(rep)

    async def run(cfg, script, **kw):
        goi.append(script)
        assert script == "send extract ETPMNV3, status", script
        return 0, status.splitlines()
    ggmod.detail, ggmod.run = detail, run
    w, run_ = _phien()
    try:
        try:
            r = asyncio.run(w._b_scn_floor(run_, {}))
        except wz.WizardError as e:
            r = e
    finally:
        ggmod.detail, ggmod.run = goc
    return r, w, goi


@test
def replicat_VANG_lay_SCN_extract_da_doc_khi_het_recovery():
    """So lieu that #21: nguong duoi = SCN ETPMNV3 da doc toi."""
    r, w, goi = _chay(REP_VANG, STATUS_XONG)
    assert not isinstance(r, Exception), r
    assert w.lay(1)["scn_floor"] == 494765488, w.lay(1)["scn_floor"]
    assert "494,765,488" in r["msg"] and "chưa từng áp" in r["msg"], r["msg"]
    assert len(goi) == 1


@test
def replicat_VANG_ma_extract_DANG_recovery_thi_DUNG():
    """Dang recovery, vi tri doc con lui ve giao dich mo luc stop - khong lay."""
    r, w, _ = _chay(REP_VANG, STATUS_RECOVERY)
    assert isinstance(r, wz.WizardError), r
    assert "chưa hết recovery" in str(r), str(r)
    assert w.lay(1)["scn_floor"] is None


@test
def co_high_wm_thi_giu_duong_cu_KHONG_hoi_extract():
    r, w, goi = _chay(dict(REP_VANG, low_wm="494782647", high_wm="494784168",
                           log_bsn="457943246"), STATUS_XONG)
    assert not isinstance(r, Exception), r
    assert w.lay(1)["scn_floor"] == 494784168
    assert goi == [], "co High WM ma van hoi Extract"


@test
def co_low_wm_ma_mat_high_wm_van_DUNG_nhu_cu():
    """Replicat DA tung ap (co Low WM) ma khong doc duoc High WM la bat thuong
    - khong duoc roi xuong duong thay the."""
    r, w, goi = _chay(dict(REP_VANG, low_wm="494782647"), STATUS_XONG)
    assert isinstance(r, wz.WizardError), r
    assert "Không đọc được mốc High Watermark" in str(r), str(r)
    assert goi == []


if __name__ == "__main__":
    loi = 0
    for t in TESTS:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as e:  # noqa: BLE001
            loi += 1
            print("FAIL", t.__name__, type(e).__name__, e)
    print(f"{len(TESTS) - loi}/{len(TESTS)}")
    sys.exit(1 if loi else 0)
