"""Test quy trinh LUONG_MOI - tao luong moi va dua bang dau tien vao.

Khong can Oracle, khong can ggsci: bo test nay khoa THU TU BUOC va cac chot
cau truc. Thu tu buoc la hang so trong code, nen no phai la hang so trong
test - mot ai do doi cho hai buoc la bo test phai do.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as dbmod                                   # noqa: E402
from app import wizard as wz                                  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


THU_TU = [
    (1, "TIEN_DIEU_KIEN"), (2, "KHAO_SAT"), (3, "XAC_NHAN_KEYCOLS"),
    (4, "KIEM_TEN"), (5, "TAO_BANG_DICH"), (6, "KIEM_BANG_DICH"),
    (7, "ADD_TRANDATA"), (8, "TAO_PARAM"), (9, "ADD_CHECKPOINTTABLE"),
    (10, "REGISTER_EXTRACT"), (11, "ADD_EXTRACT"), (12, "ADD_EXTTRAIL"),
    (13, "ADD_REPLICAT"), (14, "DANG_KY_CAU_HINH"), (15, "START_EXTRACT"),
    (16, "CHO_BAT_KIP"), (17, "SCN_FLOOR"), (18, "INDEX_UNUSABLE"),
    (19, "SINH_SCRIPT_NAP"), (20, "NAP"), (21, "INDEX_REBUILD"),
    (22, "GATHER_STATS"), (23, "START_REPLICAT_2"), (24, "CHO_LOW_WM"),
    (25, "STOP_REPLICAT"), (26, "BO_HANDLECOLLISIONS"),
    (27, "START_REPLICAT_3"), (28, "VERIFY"), (29, "KIEM_CHUNG"),
]


def _so(ten: str) -> int:
    return next(s.no for s in wz.LUONG_MOI if s.name == ten)


@test
def thu_tu_buoc_la_HANG_SO():
    assert [(s.no, s.name) for s in wz.LUONG_MOI] == THU_TU


@test
def cac_quan_he_thu_tu_khong_duoc_dao():
    # register truoc add extract; checkpointtable truoc replicat
    assert _so("REGISTER_EXTRACT") < _so("ADD_EXTRACT")
    assert _so("ADD_CHECKPOINTTABLE") < _so("ADD_REPLICAT")
    # cot khoa phai chot TRUOC khi sinh file tham so (MAP nhung KEYCOLS)
    assert _so("XAC_NHAN_KEYCOLS") < _so("TAO_PARAM")
    # bang dich va quyen phai xong TRUOC khi cham DB nguon
    assert _so("KIEM_BANG_DICH") < _so("REGISTER_EXTRACT")
    # dang ky cau hinh sau khi co du doi tuong, truoc khi dieu khien chung
    assert _so("ADD_REPLICAT") < _so("DANG_KY_CAU_HINH") < _so("START_EXTRACT")
    # chong trung: bo SAU khi da nap va da cho ap het phan chong lan
    assert (_so("NAP") < _so("START_REPLICAT_2") < _so("CHO_LOW_WM")
            < _so("STOP_REPLICAT") < _so("BO_HANDLECOLLISIONS")
            < _so("START_REPLICAT_3") < _so("VERIFY"))
    # nguong duoi doc truoc khi sinh script nap
    assert _so("SCN_FLOOR") < _so("SINH_SCRIPT_NAP") < _so("NAP")


@test
def KHONG_co_buoc_them_map_them_table():
    """File tham so sinh o TAO_PARAM da co san ca hai dong.

    Chay them THEM_MAP la NHAN DOI dong MAP - hong im lang.
    """
    ten = {s.name for s in wz.LUONG_MOI}
    for xau in ("THEM_MAP", "THEM_TABLE", "STOP_EXTRACT",
                "THEM_HANDLECOLLISIONS", "TRUNCATE", "XAC_NHAN_LECH"):
        assert xau not in ten, xau


@test
def moi_buoc_deu_phan_giai_duoc_handler():
    """Bat loi quen viet handler - nhu `_b_start_replicat_3`."""
    tt = wz.TIEN_TO["LUONG_MOI"]
    for s in wz.LUONG_MOI:
        fn = (getattr(wz.Wizard, f"_b_{tt}{s.name.lower()}", None)
              or getattr(wz.Wizard, f"_b_{s.name.lower()}", None))
        assert fn is not None, f"bước {s.no} {s.name} chưa có phần thực thi"


@test
def buoc_chay_replicat_KHONG_dinh_vao_handler_ns():
    """Tien to `ns_` phan giai TRUOC, nen ten buoc phai tranh trung."""
    tt = wz.TIEN_TO["LUONG_MOI"]
    for ten in ("START_REPLICAT_2", "START_REPLICAT_3"):
        assert getattr(wz.Wizard, f"_b_{tt}{ten.lower()}", None) is None
        assert (getattr(wz.Wizard, f"_b_{ten.lower()}")
                is wz.Wizard._b_start_replicat)


@test
def muc_L2_phu_dung_cac_buoc_khong_hoan_tac_duoc():
    muc = {s.name: s.level for s in wz.LUONG_MOI}
    for ten in ("TAO_BANG_DICH", "ADD_CHECKPOINTTABLE", "REGISTER_EXTRACT",
                "ADD_EXTRACT", "ADD_EXTTRAIL", "ADD_REPLICAT",
                "DANG_KY_CAU_HINH", "NAP", "BO_HANDLECOLLISIONS"):
        assert muc[ten] == wz.L2, f"{ten} phải là L2"


# ----------------------------------------------------- engine + khoa phien
class _CfgGia:
    streams = {"HT": {"extract": "ETHT", "replicat": "RHT", "trail": "ht"}}


def _wizard():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(dbmod.SCHEMA)
    return wz.Wizard(_CfgGia(), con, None, None, None), con


def _them_bang(con, ten="HT_MA_KENH_BAN", **kw):
    d = dict(src_owner="KT_BSH", src_table=ten, tgt_owner="ODS", tgt_table=ten,
             stream=None, prepared=0, in_extract=0, has_map=0)
    d.update(kw)
    con.execute(
        "INSERT INTO managed_table(src_owner,src_table,tgt_owner,tgt_table,"
        "stream,prepared,in_extract,has_map) VALUES"
        "(:src_owner,:src_table,:tgt_owner,:tgt_table,:stream,:prepared,"
        ":in_extract,:has_map)", d)


@test
def pf_cua_buoc_KHONG_no_khi_luong_chua_ton_tai():
    """Luong chua dang ky -> `cfg.streams[None]` se KeyError neu moc sai."""
    w, con = _wizard()
    _them_bang(con)
    rid = w.tao("LUONG_MOI", stream=None, src_table="HT_MA_KENH_BAN", who="t")
    co_pf = []
    for s in wz.LUONG_MOI:
        op, target = w.pf_cua_buoc(rid, s.no)
        if op:
            co_pf.append((s.name, op, target))
    assert co_pf == [("ADD_TRANDATA", "ADD_TRANDATA", "HT_MA_KENH_BAN")], co_pf


@test
def dang_ky_cau_hinh_xong_thi_cac_buoc_sau_co_pre_flight_that():
    """Moc theo `stream`, nen buoc 14 dat stream la nua sau co pre-flight."""
    w, con = _wizard()
    _them_bang(con)
    rid = w.tao("LUONG_MOI", stream=None, src_table="HT_MA_KENH_BAN", who="t")
    con.execute("UPDATE wizard_run SET stream='HT' WHERE id=?", (rid,))
    op, target = w.pf_cua_buoc(rid, _so("START_REPLICAT_3"))
    assert (op, target) == ("START", "RHT"), (op, target)
    op, target = w.pf_cua_buoc(rid, _so("STOP_REPLICAT"))
    assert (op, target) == ("STOP", "RHT"), (op, target)


@test
def khoa_doc_quyen_phu_CA_HAI_loai_tao_luong_moi():
    """Hai phien cung cham DB nguon production -> toi da MOT cai."""
    w, con = _wizard()
    _them_bang(con, "HT_MA_KENH_BAN")
    _them_bang(con, "HT_MA_KVUC")
    w.tao("NEW_STREAM", stream=None, src_table="HT_MA_KENH_BAN", who="t")
    try:
        w.tao("LUONG_MOI", stream=None, src_table="HT_MA_KVUC", who="t")
    except wz.WizardError as e:
        assert "Tối đa MỘT phiên" in str(e), str(e)
        return
    raise AssertionError("mo duoc hai phien tao luong moi cung luc")


@test
def khoa_doc_quyen_cung_chan_theo_chieu_nguoc_lai():
    w, con = _wizard()
    _them_bang(con, "HT_MA_KENH_BAN")
    _them_bang(con, "HT_MA_KVUC")
    w.tao("LUONG_MOI", stream=None, src_table="HT_MA_KENH_BAN", who="t")
    try:
        w.tao("NEW_STREAM", stream=None, src_table="HT_MA_KVUC", who="t")
    except wz.WizardError:
        return
    raise AssertionError("mo duoc NEW_STREAM khi dang co LUONG_MOI")


@test
def bang_da_co_MAP_thi_khong_mo_duoc_luong_moi():
    w, con = _wizard()
    _them_bang(con, "CN_CH", has_map=1, stream="PM")
    try:
        w.tao("LUONG_MOI", stream=None, src_table="CN_CH", who="t")
    except wz.WizardError as e:
        assert "đã có dòng MAP" in str(e), str(e)
        return
    raise AssertionError("bang da thuoc luong khac ma van mo duoc")


@test
def phien_LUONG_MOI_luu_voi_stream_rong():
    """Luong chua ton tai thi cot `stream` phai NULL, khong duoc doan."""
    w, con = _wizard()
    _them_bang(con)
    rid = w.tao("LUONG_MOI", stream="HT", src_table="HT_MA_KENH_BAN", who="t")
    r = con.execute("SELECT kind, stream FROM wizard_run WHERE id=?",
                    (rid,)).fetchone()
    assert r["kind"] == "LUONG_MOI" and r["stream"] is None, dict(r)
