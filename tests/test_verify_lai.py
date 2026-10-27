"""Test buoc VERIFY doi chieu LAI khi lech o che do LIVE.

2026-10-23, luong PMNV3: lo 10, 15, 16 dung o VERIFY vi lech 1-2 dong tren
bang dang co giao dich lien tuc (BH_2B_APIGCN, BH_HD_GOC_NB, BH_HT_SO_TT,
BH_HD_TTRANG). Dem lai vai giay sau deu khop, discard sach -> lech oan.

Khong can Oracle: verifier gia, dong ho va sleep cua app/cho.py gia.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as dbmod                                   # noqa: E402
from app import cho as chomod                                 # noqa: E402
from app import wizard as wz                                  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class _Cfg:
    streams = {"PMNV3": {"extract": "ETPMNV3", "replicat": "RPMNV3", "trail": "pp"}}

    def __init__(self, tmp: Path):
        self.gg_home = tmp


class _Ver:
    """Tra lan luot cac cap (nguon, dich) da cho. Moi lan dem ton `ton` giay
    tren dong ho gia."""

    def __init__(self, cap: list[tuple[int, int]], gio: list[float], ton: float):
        self.cap = list(cap)
        self.goi = 0
        self.gio = gio
        self.ton = ton

    async def verify_table(self, src_table, **kw):
        n, d = self.cap[self.goi]
        self.goi += 1
        self.gio[0] += self.ton
        return {"src_count": n, "tgt_count": d, "src_hash": "h", "tgt_hash": "h",
                "matched": int(n == d), "col_count": 5}


def _chay(cap, ton: float = 0.0):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(dbmod.SCHEMA)
    con.execute(
        "INSERT INTO wizard_run(kind,state,src_owner,src_table,tgt_owner,tgt_table,"
        "stream,created_at,created_by,updated_at,cur_step,ctx_json)"
        " VALUES ('ADD_TABLE','OPEN','KT_BSH','BH_HT_SO_TT','ODS','BH_HT_SO_TT',"
        "'PMNV3',1,'t',1,24,'{}')")
    w = wz.Wizard(_Cfg(Path(tempfile.mkdtemp())), con, None, None, None)
    ngu: list[float] = []
    gio = [0.0]
    v = _Ver(cap, gio, ton)
    w.verifier = v

    async def sleep(s):
        ngu.append(s)
        gio[0] += s
    goc = (chomod.asyncio.sleep, chomod.time.monotonic)
    chomod.asyncio.sleep, chomod.time.monotonic = sleep, lambda: gio[0]
    try:
        try:
            r = asyncio.run(w._b_verify(w.lay(1), {}))
        except wz.WizardError as e:
            r = e
    finally:
        chomod.asyncio.sleep, chomod.time.monotonic = goc
    return r, v.goi, ngu


@test
def khop_ngay_thi_doi_chieu_MOT_lan():
    r, goi, ngu = _chay([(3057562, 3057562)])
    assert not isinstance(r, Exception), r
    assert goi == 1 and ngu == []


@test
def lech_oan_roi_khop_thi_PASS():
    """So that BH_HT_SO_TT: lech 1 roi khop."""
    r, goi, ngu = _chay([(3057555, 3057554), (3057562, 3057562)])
    assert not isinstance(r, Exception), r
    assert goi == 2 and len(ngu) == 1
    assert any("lệch" in x for x in r["output"]), r["output"]


@test
def lan_nao_cung_lech_thi_FAIL_khi_het_tran():
    r, goi, ngu = _chay([(10, 9)] * 500)
    assert isinstance(r, wz.WizardError), r
    assert "VẪN LỆCH sau" in str(r), str(r)
    assert sum(ngu) == wz.VERIFY_TRAN_S, sum(ngu)   # dung tran, khong lo
    assert goi == len(ngu) + 1


@test
def bang_lon_dem_qua_tran_van_doi_chieu_du_so_lan_toi_thieu():
    """Lo 17 PMNV3: bang 12 trieu dong, mot lan dem 99 giay > tran 60 giay
    -> truoc day chi dem MOT lan roi bao lech oan."""
    r, goi, ngu = _chay([(12064716, 12064715), (12064720, 12064720)], ton=99)
    assert not isinstance(r, Exception), r
    assert goi == 2, goi


@test
def bang_lon_lech_that_thi_FAIL_sau_dung_so_lan_toi_thieu():
    r, goi, _ = _chay([(10, 9)] * 10, ton=99)
    assert isinstance(r, wz.WizardError), r
    assert goi == wz.VERIFY_TOI_THIEU, goi
    assert f"sau {wz.VERIFY_TOI_THIEU} lần" in str(r), str(r)


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
