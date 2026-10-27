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
    (1, "TIEN_DIEU_KIEN"), (2, "DANG_KY_BANG"), (3, "KHAO_SAT"),
    (4, "XAC_NHAN_KEYCOLS"), (5, "KIEM_TEN"), (6, "TAO_BANG_DICH"),
    (7, "KIEM_BANG_DICH"), (8, "ADD_TRANDATA"), (9, "TAO_PARAM"),
    (10, "ADD_CHECKPOINTTABLE"), (11, "REGISTER_EXTRACT"), (12, "ADD_EXTRACT"),
    (13, "ADD_EXTTRAIL"), (14, "ADD_REPLICAT"), (15, "DANG_KY_CAU_HINH"),
    (16, "START_EXTRACT"), (17, "CHO_BAT_KIP"), (18, "SCN_FLOOR"),
    (19, "INDEX_UNUSABLE"), (20, "SINH_SCRIPT_NAP"), (21, "NAP"),
    (22, "INDEX_REBUILD"), (23, "GATHER_STATS"), (24, "START_REPLICAT_2"),
    (25, "CHO_LOW_WM"), (26, "STOP_REPLICAT"), (27, "BO_HANDLECOLLISIONS"),
    (28, "START_REPLICAT_3"), (29, "VERIFY"), (30, "KIEM_CHUNG"),
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
    # dang ky bang TRUOC moi buoc khac: registry la allowlist lop 5, va buoc
    # DANG_KY_CAU_HINH UPDATE chinh dong do - khong co dong thi no cap nhat
    # 0 hang va im lang.
    assert _so("DANG_KY_BANG") == 2
    assert all(_so("DANG_KY_BANG") < s.no for s in wz.LUONG_MOI
               if s.name not in ("TIEN_DIEU_KIEN", "DANG_KY_BANG"))


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


@test
def mo_duoc_phien_khi_bang_CHUA_dang_ky():
    """Buoc 2 moi dang ky bang, nen `tao` khong duoc doi co san dong.

    Doi la mot VONG KHONG THE DI: o dang ky bang bat chon mot luong DA CO
    trong app.toml, ma luong cua quy trinh nay chi ton tai tu buoc
    DANG_KY_CAU_HINH.
    """
    w, con = _wizard()                       # KHONG goi _them_bang
    rid = w.tao("LUONG_MOI", stream=None, src_table="HT_MA_KVUC", who="t")
    r = con.execute("SELECT src_owner, src_table, tgt_owner, tgt_table, stream"
                    " FROM wizard_run WHERE id=?", (rid,)).fetchone()
    assert dict(r) == {"src_owner": "KT_BSH", "src_table": "HT_MA_KVUC",
                       "tgt_owner": "ODS", "tgt_table": "HT_MA_KVUC",
                       "stream": None}, dict(r)
    # va KHONG tu ghi dong nao vao danh sach quan ly - do la viec cua buoc 2
    assert con.execute("SELECT COUNT(*) FROM managed_table").fetchone()[0] == 0


@test
def cac_quy_trinh_KHAC_van_doi_bang_da_dang_ky():
    """Noi long chi cho LUONG_MOI. Cac duong khac giu nguyen chot lop 5."""
    for kind in ("RESYNC_TABLE", "NEW_STREAM", "GO_BANG"):
        w, _ = _wizard()
        try:
            w.tao(kind, stream="HT", src_table="HT_MA_KVUC", who="t")
        except wz.WizardError as e:
            assert "không nằm trong danh sách quản lý" in str(e), (kind, str(e))
            continue
        raise AssertionError(f"{kind} mo duoc phien cho bang chua dang ky")


@test
def buoc_dang_ky_bang_CHI_co_o_LUONG_MOI_va_ADD_TABLE():
    """Hai quy trinh dua bang MOI vao luong thi tu dang ky, va dang ky la
    buoc DAU TIEN co cham bang (truoc khao sat - registry la allowlist)."""
    assert wz.so_buoc("ADD_TABLE", "DANG_KY_BANG") == 1
    assert wz.so_buoc("LUONG_MOI", "DANG_KY_BANG") == 2
    for kind in ("RESYNC_TABLE", "NEW_STREAM", "GO_BANG"):
        assert wz.so_buoc(kind, "DANG_KY_BANG") is None, kind


@test
def so_buoc_va_tong_buoc_khop_bo_buoc():
    """UI lay so hieu buoc tu day chu khong go cung - chot cho chinh no."""
    assert wz.tong_buoc("LUONG_MOI") == len(THU_TU) == 30
    for no, ten in THU_TU:
        assert wz.so_buoc("LUONG_MOI", ten) == no, ten
    assert wz.so_buoc("LUONG_MOI", "KHONG_CO_BUOC_NAY") is None


# ============================== su co 2026-10-20: buoc 24 bi chan oan
#
# LUONG_MOI tao Replicat o ADD_REPLICAT roi chay no o START_REPLICAT_2. Voi
# bang nho hai buoc do cach nhau BAY GIAY - ngan hon chu ky do cua bo thu
# thap. Pre-flight doc anh so lieu, khong thay RPMNV2, ket luan "khong ton
# tai" -> luot dung -> tu don thao sach ca mot luong vua dung.
import asyncio                                                # noqa: E402

from app import preflight as pf                               # noqa: E402
from app.adapters import gg as ggmod                          # noqa: E402


class _ProcGia:
    def __init__(self, ten, status="STOPPED"):
        self.name = ten
        self.program = "REPLICAT" if ten.startswith("R") else "EXTRACT"
        self.status = status
        self.lag_s = 0

    @property
    def is_running(self):
        return self.status == "RUNNING"


class _BoThuThapGia:
    """Anh so lieu CU: chua biet tien trinh vua duoc tao."""
    def __init__(self, procs):
        self.snap = type("S", (), {"procs": procs, "ts": 1, "age_s": 0})()


def _pre(anh_so_lieu, info_all_that):
    p = pf.Preflight(_CfgGia(), None, None, _BoThuThapGia(anh_so_lieu))
    goc = ggmod.info_all

    async def gia(cfg):
        return info_all_that
    ggmod.info_all = gia
    return p, (lambda: setattr(ggmod, "info_all", goc))


@test
def tien_trinh_VUA_TAO_khong_bi_coi_la_khong_ton_tai():
    moi = _ProcGia("RPMNV2")
    p, tra_lai = _pre([_ProcGia("RHT", "RUNNING")], [_ProcGia("RHT", "RUNNING"), moi])
    try:
        r = asyncio.run(p._proc_that("RPMNV2"))
        assert r is moi, "anh so lieu khong co ten -> phai doc `info all` that"
        # va pre-flight START phai KHONG con chan
        kq = asyncio.run(p._start("RPMNV2"))
        assert all(c.ok for c in kq), [(c.ten, c.note) for c in kq if not c.ok]
    finally:
        tra_lai()


@test
def tien_trinh_khong_co_o_DAU_CA_thi_van_chan():
    """Noi long khong duoc bien thanh bo chot: ten la van phai bi tu choi."""
    p, tra_lai = _pre([], [])
    try:
        assert asyncio.run(p._proc_that("KHONG_CO_THAT")) is None
        kq = asyncio.run(p._start("KHONG_CO_THAT"))
        assert kq and not kq[0].ok, kq
    finally:
        tra_lai()


@test
def anh_so_lieu_co_san_thi_KHONG_goi_info_all():
    """Doc that chi la duong du phong - khong duoc thanh moi request mot lan."""
    cu = _ProcGia("RHT", "RUNNING")
    goi = []
    p = pf.Preflight(_CfgGia(), None, None, _BoThuThapGia([cu]))
    goc = ggmod.info_all

    async def gia(cfg):
        goi.append(1)
        return []
    ggmod.info_all = gia
    try:
        assert asyncio.run(p._proc_that("RHT")) is cu
        assert goi == [], "da co trong anh so lieu ma van goi info all"
    finally:
        ggmod.info_all = goc


# ================= su co #6 (tiep): trang /wizard/<id> sap, don khong chiu
# chay lai, va don that bai van dong phien.
import tempfile                                               # noqa: E402

from app import toml_ghi as tgmod                             # noqa: E402


@test
def luong_mat_khoi_cau_hinh_KHONG_lam_sap_trang_phien():
    """Phien ghi stream=PMNV ma app.toml khong con PMNV -> truoc day KeyError
    o pf_cua_buoc va CA TRANG /wizard/6 tra 500, nut Don sach khong hien."""
    w, con = _wizard()
    rid = w.tao("LUONG_MOI", stream=None, src_table="BC_BH_NHANH_DTHU", who="t")
    con.execute("UPDATE wizard_run SET stream='PMNV' WHERE id=?", (rid,))
    op, target = w.pf_cua_buoc(rid, _so("START_REPLICAT_2"))
    assert (op, target) == ("LUONG_MAT", "PMNV"), (op, target)
    # va pre-flight do CHAN, khong cho chay tiep
    pre = pf.Preflight(_CfgGia(), None, None, _BoThuThapGia([]))
    kq = asyncio.run(pre._luong_mat("PMNV"))
    assert kq and not kq[0].ok and kq[0].blocking
    # buoc dung `_proc(run)` bao loi RO RANG, khong KeyError
    try:
        w._proc(w.lay(rid))
    except wz.WizardError as e:
        assert "KHÔNG còn trong conf/app.toml" in str(e), str(e)
    else:
        raise AssertionError("_proc khong bao loi khi luong da mat")


def _phien_da_don_tay(capture_con: bool, tien_trinh_con: set):
    """Dung lai DUNG hinh dang phien #6 sau khi dọn tay mot nua."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "dirprm").mkdir()
    (tmp / "dirdat").mkdir()
    for f in ("etpmnv2.prm", "rpmnv2.prm"):
        (tmp / "dirprm" / f).write_text("x")

    class Cfg(_CfgGia):
        gg_home = tmp
        app_toml = tmp / "app.toml"
        streams = dict(_CfgGia.streams)

        def param_file(self, ten):
            return tmp / "dirprm" / f"{ten.lower()}.prm"

    class Ora:
        async def scalar(self, sql, **b):
            return 1 if capture_con else 0

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(dbmod.SCHEMA)
    w = wz.Wizard(Cfg(), con, Ora(), None, None)
    _them_bang(con, "BC_BH_NHANH_DTHU")
    rid = w.tao("LUONG_MOI", stream=None, src_table="BC_BH_NHANH_DTHU", who="t")
    con.execute("UPDATE wizard_run SET stream='PMNV', created_extract='ETPMNV2',"
                " created_replicat='RPMNV2', created_trail_prefix='pa',"
                " created_ckpt_table='OGG_BSH.RPMNV2', cur_step=24 WHERE id=?", (rid,))
    con.execute("UPDATE managed_table SET stream='PMNV' WHERE src_table='BC_BH_NHANH_DTHU'")

    goc = (ggmod.stop_process, ggmod.run_pham_vi, ggmod.info_all,
           tgmod.bo_dang_ky_luong)

    async def stop(cfg, t, **k):
        raise ggmod.Blocked(f"Process ngoai enum: {t!r}")

    async def pham_vi(cfg, script, cp, **k):
        return 0, ["Error: group does not exist."]

    async def info(cfg):
        return [_ProcGia(x) for x in tien_trinh_con]

    ggmod.stop_process, ggmod.run_pham_vi, ggmod.info_all = stop, pham_vi, info
    tgmod.bo_dang_ky_luong = lambda cfg, sid: {"backup": None}

    def tra_lai():
        (ggmod.stop_process, ggmod.run_pham_vi, ggmod.info_all,
         tgmod.bo_dang_ky_luong) = goc
    return w, con, rid, tmp, tra_lai


@test
def don_lai_phien_DA_DON_TAY_thi_dong_phien_sach_se():
    """Moi lenh deu 'does not exist' - KHONG phai loi. Kiem ket qua that:
    info all khong con, dba_capture khong con -> xoa file, reset registry,
    dong phien ABORTED."""
    w, con, rid, tmp, tra_lai = _phien_da_don_tay(False, set())
    try:
        r = asyncio.run(w.rollback_new_stream(rid, "t", "don lai phien 6"))
        assert "Đã dọn sạch" in r["msg"], r["msg"]
        assert w.lay(rid)["state"] == "ABORTED"
        assert not list((tmp / "dirprm").iterdir()), "file tham so phai bi xoa"
        m = con.execute("SELECT stream FROM managed_table"
                        " WHERE src_table='BC_BH_NHANH_DTHU'").fetchone()
        assert m["stream"] is None, "registry phai tro lai chua thuoc luong nao"
    finally:
        tra_lai()


@test
def capture_VAN_CON_o_nguon_thi_GIU_phien_mo_va_giu_file():
    """Lenh `unregister` in ERROR nhung `gg.run()` khong nem loi -> truoc day
    bi tinh la 'da chay', phien dong ABORTED va nut Don sach BIEN MAT trong
    khi capture mo coi van giu archive log."""
    w, con, rid, tmp, tra_lai = _phien_da_don_tay(True, set())
    try:
        try:
            asyncio.run(w.rollback_new_stream(rid, "t", "don lai phien 6"))
        except wz.WizardError as e:
            assert "OGG$CAP_ETPMNV2" in str(e), str(e)
        else:
            raise AssertionError("capture con ma bao don sach")
        assert w.lay(rid)["state"] == "OPEN", "phai giu OPEN de con bam don lai"
        assert len(list((tmp / "dirprm").iterdir())) == 2, "phai GIU file tham so"
    finally:
        tra_lai()


@test
def tien_trinh_VAN_CON_trong_info_all_thi_cung_chua_sach():
    w, con, rid, tmp, tra_lai = _phien_da_don_tay(False, {"ETPMNV2"})
    try:
        try:
            asyncio.run(w.rollback_new_stream(rid, "t", "don lai phien 6"))
        except wz.WizardError as e:
            assert "ETPMNV2" in str(e), str(e)
        else:
            raise AssertionError("tien trinh con ma bao don sach")
        assert w.lay(rid)["state"] == "OPEN"
    finally:
        tra_lai()


@test
def TRANDATA_da_bat_KHONG_chan_luot_chay_lai():
    """Lan don co y KHONG tat TRANDATA -> luot chay lai gap bang da prepared.
    Handler da tu bo qua; pre-flight ma CHAN thi luot chet o buoc 8."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dbmod.SCHEMA)
    _them_bang(c, "BC_BH_NHANH_DTHU", prepared=1)
    pre = pf.Preflight(_CfgGia(), c, None, _BoThuThapGia([]))
    kq = asyncio.run(pre._add_trandata("BC_BH_NHANH_DTHU"))
    chan = [x for x in kq if x.blocking and not x.ok]
    assert not chan, [(x.name, x.detail) for x in chan]


@test
def lenh_xoa_checkpointtable_PHAI_co_dau_cham_than():
    """Khong co `!` GGSCI hoi Y/N, nhan EOF va LAP VO TAN o 100% CPU - goc
    cua hai `ggsci` treo trong su co #6. Moi script cua duong don co
    'delete checkpointtable' deu phai ket thuc bang ' !'."""
    w, con, rid, tmp, tra_lai = _phien_da_don_tay(False, set())
    da_chay = []

    async def ghi(cfg, script, cp, **k):
        da_chay.append(script)
        return 0, ["Error: group does not exist."]
    ggmod.run_pham_vi = ghi
    try:
        asyncio.run(w.rollback_new_stream(rid, "t", "kiem lenh don"))
    finally:
        tra_lai()
    ck = [l.strip() for sc in da_chay for l in sc.splitlines()
          if "delete checkpointtable" in l.lower()]
    assert ck, "duong don khong xoa checkpointtable"
    assert all(l.endswith(" !") for l in ck), ck


# ============ su co #6 (goc): ggsci hoi Y/N, in vo tan, OOM giet app 2 lan
import subprocess as _sp                                      # noqa: E402
import time as _time                                          # noqa: E402

_PY = sys.executable


def _goi(ma: str, **k):
    return ggmod.chay_co_rao([_PY, "-c", ma], "exit\n", cwd=".", env=None,
                             timeout=k.get("timeout", 30))


@test
def ggsci_hoi_YN_lap_vo_tan_thi_bi_GIET_NGAY():
    """Dung y het hanh vi da gap: hoi, nhan EOF, hoi lai, mai mai."""
    ma = ("while True:\n"
          "    print('Are you sure you want to delete this checkpoint table? "
          "Invalid response. Must be [Y]es or [N]o.', flush=True)\n")
    t0 = _time.monotonic()
    try:
        _goi(ma)
    except ggmod.GgsciTreo as e:
        assert "[Y]es/[N]o" in str(e), str(e)
    else:
        raise AssertionError("ggsci hoi Y/N ma khong bi giet")
    assert _time.monotonic() - t0 < 10, "phai giet trong vai giay, khong doi het timeout"


@test
def output_vuot_tran_thi_bi_giet_va_KHONG_vao_RAM():
    goc = ggmod.TRAN_OUTPUT
    ggmod.TRAN_OUTPUT = 256 * 1024
    try:
        _goi("import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n")
    except ggmod.GgsciTreo as e:
        assert "MB" in str(e), str(e)
    else:
        raise AssertionError("in vo tan ma khong bi giet")
    finally:
        ggmod.TRAN_OUTPUT = goc


@test
def qua_gio_thi_bi_giet():
    try:
        _goi("import time\ntime.sleep(60)\n", timeout=1)
    except ggmod.GgsciTreo as e:
        assert "quá 1s" in str(e), str(e)
    else:
        raise AssertionError("treo ma khong bi giet")


@test
def lenh_binh_thuong_van_doc_du_output_va_ma_thoat():
    rc, text = _goi("import sys\nprint('EXTRACT RUNNING ETHT')\n"
                    "print(sys.stdin.read().strip())\nsys.exit(3)\n")
    assert rc == 3, rc
    assert "EXTRACT RUNNING ETHT" in text and "exit" in text, text


# ================= phien #7: EXTTRAIL tuong doi, buoc 17 qua som, buoc 25 ket
class _DongHoGia:
    """Thay time.monotonic: moi lan cho_den goi do(), dong ho tien 10 giay."""
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _chay_cho(w, run, ham, dong_ho, so_lan=8):
    """Thay cho.cho_den: goi do() toi da so_lan, dong ho tien 10s moi lan."""
    goc_cho, goc_mono = wz.cho.cho_den, wz.time.monotonic

    async def cho_gia(do, tran_s, nhan):
        cuoi = ""
        for _ in range(so_lan):
            xong, cuoi = await do()
            if xong:
                return {"xong": True, "giay": 0, "cuoi": cuoi, "output": [cuoi]}
            dong_ho.t += 10
        return {"xong": False, "giay": 1200, "cuoi": cuoi, "output": [cuoi]}
    wz.cho.cho_den, wz.time.monotonic = cho_gia, dong_ho
    try:
        return asyncio.run(ham(run, {}))
    finally:
        wz.cho.cho_den, wz.time.monotonic = goc_cho, goc_mono


def _phien_buoc25(tmp, low, high, rba, co_file_sau=False):
    (tmp / "dirdat").mkdir(exist_ok=True)
    f = tmp / "dirdat" / "pa000000000"
    f.write_bytes(b"x" * 2611)
    if co_file_sau:
        (tmp / "dirdat" / "pa000000001").write_bytes(b"x")

    class Cfg(_CfgGia):
        gg_home = tmp
        streams = {"PMNV2": {"extract": "ETPMNV2", "replicat": "RPMNV2", "trail": "pa"}}
    w = wz.Wizard(Cfg(), None, None, None, None)
    run = {"maxscn": 380628390, "stream": "PMNV2"}
    goc = (ggmod.detail, ggmod.status_of)

    async def detail(cfg, proc):
        return {"low_wm": low, "high_wm": high, "read_file": str(f), "rba": rba}

    async def status_of(cfg, proc):
        # Duong thu ba (ap rong, 2026-10-21) hoi trang thai Replicat. Wizard
        # o day khong co ket noi DB dich -> duong do khong bao gio dat.
        return type("St", (), {"is_running": True, "status": "RUNNING", "lag_s": 0})()
    ggmod.detail, ggmod.status_of = detail, status_of
    return w, run, (lambda: (setattr(ggmod, "detail", goc[0]),
                             setattr(ggmod, "status_of", goc[1])))


@test
def prm_extract_dung_duong_dan_TUYET_DOI_khop_add_exttrail():
    """`./dirdat/pa` trong .prm ma dang ky `/u01/app/ogg/dirdat/pa` -> OGG-01044."""
    class Cfg(_CfgGia):
        gg_home = Path("/u01/app/ogg")
    w = wz.Wizard(Cfg(), None, None, None, None)
    run = {"created_extract": "ETPMNV2", "created_replicat": "RPMNV2",
           "created_trail_prefix": "pa", "created_ckpt_table": "OGG_BSH.RPMNV2",
           "src_owner": "KT_BSH", "src_table": "BC_BH_NHANH_DTHU"}
    prm = w._prm_extract(run)
    dong = [l for l in prm.splitlines() if l.upper().startswith("EXTTRAIL")]
    assert dong == [f"EXTTRAIL {Path('/u01/app/ogg')}/dirdat/pa"], dong
    assert "./dirdat" not in prm


@test
def buoc25_low_wm_RONG_high_vuot_doc_het_trail_thi_QUA_sau_30s():
    """Dung so lieu that cua RPMNV2 luc 16:3x ngay 2026-10-20."""
    tmp = Path(tempfile.mkdtemp())
    w, run, tra = _phien_buoc25(tmp, None, "380939029", "2611")
    try:
        r = _chay_cho(w, run, w._b_cho_low_wm, _DongHoGia())
    finally:
        tra()
    assert "đã đọc hết trail" in r["msg"] and "30s/30s" in r["msg"], r["msg"]


@test
def buoc25_low_wm_rong_nhung_CHUA_doc_het_trail_thi_KHONG_qua():
    for rba, co_sau in (("1342", False), ("2611", True)):
        tmp = Path(tempfile.mkdtemp())
        w, run, tra = _phien_buoc25(tmp, None, "380939029", rba, co_sau)
        try:
            _chay_cho(w, run, w._b_cho_low_wm, _DongHoGia())
        except wz.WizardError:
            continue
        finally:
            tra()
        raise AssertionError(f"cho qua khi chua doc het trail (rba={rba}, file sau={co_sau})")


@test
def buoc25_low_wm_rong_high_CHUA_vuot_moc_nap_thi_KHONG_qua():
    tmp = Path(tempfile.mkdtemp())
    w, run, tra = _phien_buoc25(tmp, None, "380000000", "2611")
    try:
        _chay_cho(w, run, w._b_cho_low_wm, _DongHoGia())
    except wz.WizardError:
        return
    finally:
        tra()
    raise AssertionError("High WM chua vuot moc nap ma van cho qua")


@test
def buoc25_luong_BAN_van_qua_ngay_bang_low_wm():
    tmp = Path(tempfile.mkdtemp())
    w, run, tra = _phien_buoc25(tmp, "381038167", "381039669", "1")
    try:
        r = _chay_cho(w, run, w._b_cho_low_wm, _DongHoGia(), so_lan=1)
    finally:
        tra()
    assert "381,038,167" in r["msg"], r["msg"]


def _phien_buoc17(tmp, trang_thai, co_trail):
    (tmp / "dirdat").mkdir(exist_ok=True)
    if co_trail:
        (tmp / "dirdat" / "pa000000000").write_bytes(b"x")

    class Cfg(_CfgGia):
        gg_home = tmp
    w = wz.Wizard(Cfg(), None, None, None, None)
    run = {"created_extract": "ETPMNV2", "created_replicat": "RPMNV2",
           "created_trail_prefix": "pa", "created_ckpt_table": "OGG_BSH.RPMNV2"}
    goc = ggmod.status_of

    async def status_of(cfg, ten):
        return _ProcGia(ten, trang_thai)
    ggmod.status_of = status_of
    return w, run, (lambda: setattr(ggmod, "status_of", goc))


@test
def buoc17_RUNNING_lag0_nhung_CHUA_co_trail_thi_KHONG_qua():
    """Phien #7: Extract RUNNING vai giay (lag 0) roi abend OGG-01044, buoc
    17 da cho qua sau 0,1 giay. Khong co file trail thi chua bat kip gi ca."""
    tmp = Path(tempfile.mkdtemp())
    w, run, tra = _phien_buoc17(tmp, "RUNNING", co_trail=False)
    try:
        _chay_cho(w, run, w._b_ns_cho_bat_kip, _DongHoGia())
    except wz.WizardError:
        return
    finally:
        tra()
    raise AssertionError("cho qua khi Extract chua tao duoc trail")


@test
def buoc17_RUNNING_co_trail_on_dinh_30s_thi_QUA():
    tmp = Path(tempfile.mkdtemp())
    w, run, tra = _phien_buoc17(tmp, "RUNNING", co_trail=True)
    try:
        r = _chay_cho(w, run, w._b_ns_cho_bat_kip, _DongHoGia())
    finally:
        tra()
    assert "ổn định 30s" in r["msg"], r["msg"]


# ============= phien #7 (16:44): buoc 27 co chot Low WM RIENG -> tu don ca luong
@test
def qua_moc_nap_ba_truong_hop():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "pa000000000").write_bytes(b"x" * 2611)
    f = str(tmp / "pa000000000")
    w = wz.Wizard(_CfgGia(), None, None, None, None)
    mx = 380628390
    ok, _ = w._qua_moc_nap({"low_wm": "381038167"}, mx)
    assert ok is True
    ok, m = w._qua_moc_nap({"low_wm": None, "high_wm": "380939029",
                            "read_file": f, "rba": "2611"}, mx)
    assert ok is None and "đã đọc hết trail" in m, (ok, m)
    ok, _ = w._qua_moc_nap({"low_wm": None, "high_wm": "380939029",
                            "read_file": f, "rba": "1342"}, mx)
    assert ok is False, "chua doc het trail ma cho qua"
    # Low WM TRE MOT GIAO DICH (phien #9, so lieu that 17:08): co gia tri
    # nhung chua vuot, High WM vuot, doc het trail -> duong phu (None), con
    # noi goi phai giu duoc 30s trail dung yen.
    ok, m = w._qua_moc_nap({"low_wm": "381408522", "high_wm": "381709462",
                            "read_file": f, "rba": "2611"}, 381618180)
    assert ok is None and "trễ một giao dịch" in m, (ok, m)
    # High WM CHUA vuot thi khong co duong nao
    ok, _ = w._qua_moc_nap({"low_wm": "380000000", "high_wm": "380500000",
                            "read_file": f, "rba": "2611"}, mx)
    assert ok is False


@test
def buoc27_BO_HANDLECOLLISIONS_nhan_cung_dieu_kien_voi_buoc25():
    """So lieu that luc 16:44: Low WM "(no data)", High WM vuot, doc het trail.
    Buoc 25 da cho qua; buoc 27 PHAI cung cho qua, khong FAIL 'Mốc đã áp (0)'."""
    tmp = Path(tempfile.mkdtemp())
    w, run, tra = _phien_buoc25(tmp, None, "380939029", "2611")
    run.update(src_owner="KT_BSH", src_table="BC_BH_NHANH_DTHU")
    goc_read, goc_bo = wz.paramsmod.read, wz.paramsmod.bo_handlecollisions
    wz.paramsmod.read = lambda cfg, ten: type("P", (), {"text": "MAP x;"})()
    wz.paramsmod.bo_handlecollisions = lambda text, o, t: text
    try:
        r = asyncio.run(w._b_bo_handlecollisions(run, {}))
    finally:
        tra()
        wz.paramsmod.read, wz.paramsmod.bo_handlecollisions = goc_read, goc_bo
    assert "không có chế độ chống trùng" in r["msg"], r


@test
def KHONG_handler_nao_tu_doc_low_wm_ngoai_ham_chung():
    """Hai lan trong mot toi, mot chot Low WM nam rieng o mot buoc khac roi
    lam sap ca luot. Moi cho can 'da ap qua moc nap' PHAI goi _qua_moc_nap."""
    import inspect
    src = inspect.getsource(wz.Wizard)
    dong = [l.strip() for l in src.splitlines()
            if 'get("low_wm")' in l]
    assert len(dong) == 1, dong
    ham = inspect.getsource(wz.Wizard._qua_moc_nap)
    assert 'get("low_wm")' in ham


@test
def giu_yen_DEM_LAI_khi_trail_co_du_lieu_moi():
    """Luong BAN: vi tri doc doi lien tuc -> khong bao gio giu du 30s, nen
    duong phu cua _qua_moc_nap khong bao gio dat tren luong ban."""
    dh = _DongHoGia()
    goc = wz.time.monotonic
    wz.time.monotonic = dh
    try:
        giu: dict = {}
        n = []
        for rba in ("100", "100", "100", "250", "250", "250", "250"):
            n.append(wz.Wizard._giu_yen(giu, {"read_file": "/x/pa000000001", "rba": rba}))
            dh.t += 10
    finally:
        wz.time.monotonic = goc
    assert n == [0, 10, 20, 0, 10, 20, 30], n


@test
def buoc19_low_wm_TRE_mot_giao_dich_thi_qua_sau_30s_trail_dung_yen():
    """Dung so lieu that cua phien #9 luc 17:08:10."""
    tmp = Path(tempfile.mkdtemp())
    w, run, tra = _phien_buoc25(tmp, "381408522", "381709462", "2611")
    run["maxscn"] = 381618180
    try:
        r = _chay_cho(w, run, w._b_cho_low_wm, _DongHoGia())
    finally:
        tra()
    assert "trễ một giao dịch" in r["msg"] and "30s/30s" in r["msg"], r["msg"]


@test
def doc_len_duoi_output_KHONG_lam_hong_output_cua_ggsci():
    """Con ghi lien tuc trong luc cha doc lén duoi file nhieu lan. Ban dau
    cha `seek` tren CHINH handle ma con dung de ghi -> con ghi de len output
    cu (test Y/N chap chon 1/8 lan, 2026-10-20). Output phai DU va DUNG THU TU."""
    ma = ("import time\n"
          "for i in range(3000):\n"
          "    print('L%05d' % i, flush=True)\n"
          "    if i % 300 == 0: time.sleep(0.15)\n")
    rc, text = _goi(ma)
    dong = [l for l in text.splitlines() if l.startswith("L")]
    assert rc == 0 and dong == ["L%05d" % i for i in range(3000)], (rc, len(dong), dong[:3])
