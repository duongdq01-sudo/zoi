"""Test dang ky NHIEU bang mot lan vao danh sach quan ly.

2026-10-23: lo PMNV3 231 bang phai dang ky ngoai web vi o dang ky chi nhan
mot ten. Gio nhan ca danh sach - tat ca hoac khong.

Khong can Oracle: `ora.rows` gia, tra theo danh sach bang co o nguon/dich.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as dbmod                                   # noqa: E402
from app import registry as rg                                # noqa: E402
from app.adapters.ora import BadIdentifier                    # noqa: E402
from app.wizard import tach_ten_bang                          # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class _Ora:
    def __init__(self, nguon: set[str], dich: set[str] = frozenset()):
        self.nguon, self.dich = nguon, dich
        self.sql: list[str] = []

    async def rows(self, sql, **b):
        self.sql.append(sql)
        co = self.nguon if "@DBL_PMNV" in sql else self.dich
        return [{"table_name": t} for t in b.values() if t in co]


def _con():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(dbmod.SCHEMA)
    return c


def _dk(con, ora, s, stream="PMNV3"):
    try:
        return asyncio.run(rg.dang_ky_nhieu(con, ora, tach_ten_bang(s),
                                            stream=stream, who="t"))
    except Exception as e:                                      # noqa: BLE001
        return e


def _ds(con):
    return [r["src_table"] for r in con.execute(
        "SELECT src_table FROM managed_table ORDER BY src_table")]


@test
def dan_danh_sach_nhieu_dong_thi_dang_ky_het():
    con = _con()
    r = _dk(con, _Ora({"BH_A", "BH_B", "BH_C"}, {"BH_B"}),
            "bh_a, BH_B\nBH_C;  BH_A")
    assert not isinstance(r, Exception), r
    assert r["moi"] == ["BH_A", "BH_B", "BH_C"], r       # BH_A lap -> mot lan
    assert r["co_dich"] == {"BH_B"}
    assert _ds(con) == ["BH_A", "BH_B", "BH_C"]
    row = con.execute("SELECT * FROM managed_table WHERE src_table='BH_A'").fetchone()
    assert row["stream"] == "PMNV3" and row["note"], dict(row)   # note BAT BUOC


@test
def mot_ten_khong_co_o_nguon_thi_KHONG_dang_ky_bang_nao():
    con = _con()
    r = _dk(con, _Ora({"BH_A", "BH_B"}), "BH_A BH_KHONG_CO BH_B")
    assert isinstance(r, rg.DangKyError), r
    assert "BH_KHONG_CO" in str(r) and "Không đăng ký bảng nào" in str(r)
    assert _ds(con) == []


@test
def bang_da_dang_ky_thi_bo_qua_va_bao_lai():
    con = _con()
    _dk(con, _Ora({"BH_A"}), "BH_A")
    r = _dk(con, _Ora({"BH_A", "BH_B"}), "BH_A, BH_B")
    assert r["moi"] == ["BH_B"] and r["da_co"] == ["BH_A"], r
    assert _ds(con) == ["BH_A", "BH_B"]


@test
def ten_sai_cu_phap_thi_tu_choi_truoc_khi_hoi_Oracle():
    con, ora = _con(), _Ora({"BH_A"})
    r = _dk(con, ora, "BH_A, X';DROP")
    assert isinstance(r, (BadIdentifier, ValueError)), r
    assert ora.sql == [] and _ds(con) == []


@test
def vuot_tran_thi_tu_choi():
    r = _dk(_con(), _Ora(set()), " ".join(f"B{i}" for i in range(rg.DANG_KY_TOI_DA + 1)))
    assert isinstance(r, rg.DangKyError) and "tối đa" in str(r), r


@test
def luong_rong_thi_stream_NULL():
    con = _con()
    _dk(con, _Ora({"BH_A"}), "BH_A", stream=None)
    assert con.execute("SELECT stream FROM managed_table").fetchone()[0] is None


# ================================================================ bo dang ky
def _them(con, t, **kw):
    d = dict(stream="PMNV2", in_extract=0, has_map=0, prepared=0, note="moi")
    d.update(kw)
    con.execute(
        "INSERT INTO managed_table(src_owner,src_table,tgt_owner,tgt_table,stream,"
        "in_extract,has_map,prepared,handlecollisions,note,updated_at)"
        " VALUES ('KT_BSH',?,'ODS',?,?,?,?,?,0,?,1)",
        (t, t, d["stream"], d["in_extract"], d["has_map"], d["prepared"], d["note"]))


def _bo(con, s, **kw):
    try:
        return rg.bo_dang_ky_nhieu(con, tach_ten_bang(s), **kw)
    except Exception as e:                                      # noqa: BLE001
        return e


@test
def bo_dang_ky_5_bang_khong_khoa_cua_lo_PMNV2():
    """So that 2026-10-23: 5 bang bi loai khoi phien #40 nam mai trong o them bang."""
    con = _con()
    ds = ["KT_BH_HTTD_DVI", "KT_BH_HTTD_DVI_LOG", "KT_BH_L_CT", "KT_MYBSH_SYNC",
          "KT_MYBSH_SYNC_LOG"]
    for t in ds:
        _them(con, t)
    _them(con, "KT_BCHQ", in_extract=1, has_map=1, prepared=1)
    r = _bo(con, ", ".join(ds))
    assert not isinstance(r, Exception), r
    assert sorted(x["src_table"] for x in r) == sorted(ds)
    assert _ds(con) == ["KT_BCHQ"]


@test
def chi_kiem_thi_KHONG_xoa():
    con = _con()
    _them(con, "BH_A")
    r = _bo(con, "BH_A", chi_kiem=True)
    assert [x["src_table"] for x in r] == ["BH_A"] and _ds(con) == ["BH_A"]


@test
def bang_dang_thuoc_luong_thi_tu_choi_CA_danh_sach():
    con = _con()
    _them(con, "BH_A")
    _them(con, "BH_B", has_map=1)
    r = _bo(con, "BH_A BH_B")
    assert isinstance(r, rg.BoDangKyError) and "BH_B" in str(r) and "MAP" in str(r), r
    assert _ds(con) == ["BH_A", "BH_B"]


@test
def TRANDATA_con_bat_thi_tu_choi_vi_sync_se_them_lai():
    """BC_BH_NHANH_DTHU: da go khoi PMNV2 o phien #10, TRANDATA van bat."""
    con = _con()
    _them(con, "BC_BH_NHANH_DTHU", stream=None, prepared=1)
    r = _bo(con, "BC_BH_NHANH_DTHU")
    assert isinstance(r, rg.BoDangKyError) and "TRANDATA" in str(r), r
    assert _ds(con) == ["BC_BH_NHANH_DTHU"]


@test
def bang_trong_phien_dang_mo_thi_tu_choi():
    con = _con()
    _them(con, "BH_A")
    con.execute(
        "INSERT INTO wizard_run(kind,state,src_owner,src_table,tgt_owner,tgt_table,"
        "stream,created_at,created_by,updated_at,cur_step,ctx_json,src_tables)"
        " VALUES ('ADD_TABLE','OPEN','KT_BSH','BH_X','ODS','BH_X','PMNV2',1,'t',1,3,"
        "'{}','[\"BH_X\", \"BH_A\"]')")
    r = _bo(con, "BH_A")
    assert isinstance(r, rg.BoDangKyError) and "#1" in str(r), r


@test
def ten_khong_co_trong_danh_sach_thi_tu_choi():
    r = _bo(_con(), "BH_KHONG_CO")
    assert isinstance(r, rg.BoDangKyError) and "không có trong" in str(r), r


# ===================================================== tat TRANDATA (L2)
@test
def tich_tat_TRANDATA_thi_nhan_bang_con_TRANDATA():
    con = _con()
    _them(con, "BC_BH_NHANH_DTHU", stream=None, prepared=1)
    r = _bo(con, "BC_BH_NHANH_DTHU", chi_kiem=True, tat_trandata=True)
    assert [x["src_table"] for x in r] == ["BC_BH_NHANH_DTHU"], r
    # xoa that ma prepared chua ve 0 thi KHONG xoa (chot cuoi o cau DELETE)
    _bo(con, "BC_BH_NHANH_DTHU", tat_trandata=True)
    assert _ds(con) == ["BC_BH_NHANH_DTHU"]


@test
def bang_trong_extract_doc_MOI_luong_ke_ca_LOG():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    (tmp / "etpmnv2.prm").write_text("EXTRACT ETPMNV2\nTABLE KT_BSH.KT_BCHQ;\n")
    (tmp / "etlog.prm").write_text("EXTRACT ETLOG\nTABLE KT_BSH.KT_MYBSH_SYNC;\n")

    class _C:
        streams = {"PMNV2": {"extract": "ETPMNV2"},
                   "LOG": {"extract": "ETLOG", "loai": "log"}}

        def param_file(self, ten):
            return tmp / f"{ten.lower()}.prm"
    d = rg.bang_trong_extract(_C())
    assert d == {"KT_BCHQ": "ETPMNV2", "KT_MYBSH_SYNC": "ETLOG"}, d


@test
def delete_trandata_chi_gui_DUNG_mot_lenh_va_doc_lai():
    from app.adapters import gg
    goi = []

    async def run(cfg, script, **kw):
        goi.append((script, kw.get("allow_blocked")))
        return 0, []

    async def info(cfg, o, t):
        return False, ["Logging of supplemental redo log data is disabled"]
    goc = (gg.run, gg.info_trandata)
    gg.run, gg.info_trandata = run, info
    try:
        ok, _ = asyncio.run(gg.delete_trandata(None, "KT_BSH", "BC_BH_NHANH_DTHU"))
        assert ok is True
        assert goi == [("dblogin useridalias pmnv_ogg\n"
                        "delete trandata KT_BSH.BC_BH_NHANH_DTHU", True)], goi
        for xau in ("KT_*", "A\ndelete trandata KT_BSH.B", "A;B"):
            try:
                asyncio.run(gg.delete_trandata(None, "KT_BSH", xau))
            except gg.Blocked:
                continue
            raise AssertionError(f"khong chan {xau!r}")
        try:
            asyncio.run(gg.delete_trandata(None, "ODS", "X"))
            raise AssertionError("nhan schema khac KT_BSH")
        except gg.Blocked:
            pass
    finally:
        gg.run, gg.info_trandata = goc


@test
def delete_trandata_van_bi_blocklist_chan_o_duong_thuong():
    from app.adapters import gg
    try:
        gg.check_script("dblogin useridalias pmnv_ogg\ndelete trandata KT_BSH.X")
    except gg.Blocked:
        return
    raise AssertionError("delete trandata lot qua blocklist")


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
