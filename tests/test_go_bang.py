"""Test GO_BANG (go bang khoi luong) va buoc DANG_KY_BANG cua ADD_TABLE.

Khong can Oracle, khong can ggsci. Khoa THU TU BUOC cua GO_BANG (bo TABLE
truoc MAP), cac chot o tung buoc, va migration CHECK(kind) tren dung dang
DDL dang co tren server.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auto                                          # noqa: E402
from app import db as dbmod                                   # noqa: E402
from app import params as pm                                  # noqa: E402
from app import wizard as wz                                  # noqa: E402
from app.adapters import gg as ggmod                          # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _so(ten: str) -> int:
    return wz.so_buoc("GO_BANG", ten)


class _Cfg:
    streams = {"HT": {"extract": "ETHT", "replicat": "RHT", "trail": "ht"}}

    def __init__(self, tmp: Path | None = None):
        self.gg_home = tmp or Path("/khong/co")

    def param_file(self, ten):
        return self.gg_home / "dirprm" / f"{ten.lower()}.prm"


def _wizard(tmp: Path | None = None, ora=None):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(dbmod.SCHEMA)
    return wz.Wizard(_Cfg(tmp), con, ora, None, None), con


def _them(con, ten, **kw):
    d = dict(src_owner="KT_BSH", src_table=ten, tgt_owner="ODS", tgt_table=ten,
             stream="HT", prepared=1, in_extract=1, has_map=1)
    d.update(kw)
    con.execute(
        "INSERT INTO managed_table(src_owner,src_table,tgt_owner,tgt_table,stream,"
        "prepared,in_extract,has_map) VALUES (:src_owner,:src_table,:tgt_owner,"
        ":tgt_table,:stream,:prepared,:in_extract,:has_map)", d)


# =============================================================== thu tu
@test
def thu_tu_GO_BANG_la_HANG_SO():
    assert [(s.no, s.name) for s in wz.GO_BANG] == [
        (1, "KIEM_TRA_GO"), (2, "STOP_EXTRACT"), (3, "GHI_MOC_TRAIL"),
        (4, "BO_TABLE"), (5, "START_EXTRACT"), (6, "CHO_AP_HET"),
        (7, "STOP_REPLICAT"), (8, "BO_MAP"), (9, "START_REPLICAT"),
        (10, "KIEM_CHUNG_GO")]


@test
def bo_TABLE_TRUOC_bo_MAP_va_cho_ap_het_o_giua():
    """Dao lai la cai bay so mot: Extract con TABLE, Replicat mat MAP."""
    assert (_so("STOP_EXTRACT") < _so("GHI_MOC_TRAIL") < _so("BO_TABLE")
            < _so("START_EXTRACT") < _so("CHO_AP_HET") < _so("STOP_REPLICAT")
            < _so("BO_MAP") < _so("START_REPLICAT"))


@test
def moi_buoc_GO_BANG_co_handler_va_pre_flight_dung_tien_trinh():
    w, con = _wizard()
    _them(con, "A")
    _them(con, "B")
    rid = w.tao("GO_BANG", stream="HT", src_table="B", who="t")
    for s in wz.GO_BANG:
        assert getattr(wz.Wizard, f"_b_{s.name.lower()}", None), s.name
    pf = {s.name: w.pf_cua_buoc(rid, s.no) for s in wz.GO_BANG if s.pf}
    assert pf == {"STOP_EXTRACT": ("STOP", "ETHT"), "BO_TABLE": ("PARAM_EDIT", "ETHT"),
                  "START_EXTRACT": ("START", "ETHT"), "STOP_REPLICAT": ("STOP", "RHT"),
                  "BO_MAP": ("PARAM_EDIT", "RHT"), "START_REPLICAT": ("START", "RHT")}, pf


@test
def bo_dong_la_buoc_L2():
    muc = {s.name: s.level for s in wz.GO_BANG}
    assert muc["BO_TABLE"] == wz.L2 and muc["BO_MAP"] == wz.L2


# ======================================================= dieu kien mo phien
def _con_auto():
    w, con = _wizard()
    con.execute("CREATE TABLE IF NOT EXISTS x(y)")
    return con


@test
def go_bang_tu_choi_BANG_CUOI_CUNG_cua_luong():
    con = _con_auto()
    _them(con, "CHI_MOT")
    try:
        auto.kiem_dieu_kien(con, "GO_BANG", "CHI_MOT")
    except auto.AutoError as e:
        assert "CUỐI CÙNG" in str(e), str(e)
    else:
        raise AssertionError("go duoc bang cuoi cung cua Extract")


@test
def go_bang_tu_choi_bang_KHONG_thuoc_luong_nao():
    con = _con_auto()
    _them(con, "A")
    _them(con, "TRANDATA_THOI", stream=None, in_extract=0, has_map=0)
    try:
        auto.kiem_dieu_kien(con, "GO_BANG", "TRANDATA_THOI")
    except auto.AutoError as e:
        assert "không có gì để gỡ" in str(e), str(e)
    else:
        raise AssertionError("go mot bang khong thuoc luong nao")


@test
def go_bang_cho_qua_khi_luong_con_bang_khac():
    con = _con_auto()
    _them(con, "A")
    _them(con, "B")
    r = auto.kiem_dieu_kien(con, "GO_BANG", "B")
    assert r["stream"] == "HT"


@test
def go_bang_KHONG_bi_luat_cung_ten():
    """Luat cung ten la de chong lech anh xa cot khi NAP - go bang khong nap."""
    con = _con_auto()
    _them(con, "A")
    _them(con, "B", tgt_table="B_ODS")
    assert auto.kiem_dieu_kien(con, "GO_BANG", "B")["tgt_table"] == "B_ODS"


# ================================================================ handler
def _fs(tmp: Path, et_txt: str, rep_txt: str):
    (tmp / "dirprm").mkdir(exist_ok=True)
    (tmp / "dirdat").mkdir(exist_ok=True)
    (tmp / "dirprm" / "etht.prm").write_text(et_txt, encoding="utf-8")
    (tmp / "dirprm" / "rht.prm").write_text(rep_txt, encoding="utf-8")


ET = "EXTRACT ETHT\nUSERIDALIAS pmnv_ogg\nEXTTRAIL /u01/app/ogg/dirdat/ht\nTABLE KT_BSH.A;\nTABLE KT_BSH.B;\n"
REP = ("REPLICAT RHT\nUSERIDALIAS bshc_ogg\n"
       "MAP KT_BSH.A, TARGET ODS.A, COLMAP (USEDEFAULTS);\n"
       "MAP KT_BSH.B, TARGET ODS.B, KEYCOLS (X), COLMAP (USEDEFAULTS);\n")


@test
def bo_MAP_TU_CHOI_khi_Extract_con_dong_TABLE():
    tmp = Path(tempfile.mkdtemp())
    _fs(tmp, ET, REP)                      # Extract VAN con TABLE KT_BSH.B
    w, con = _wizard(tmp)
    _them(con, "A")
    _them(con, "B")
    rid = w.tao("GO_BANG", stream="HT", src_table="B", who="t")
    con.execute("UPDATE wizard_run SET ctx_json=? WHERE id=?",
                ('{"moc_go": {"scn": 1, "file": "x", "seqno": 0, "size": 0}}', rid))
    try:
        asyncio.run(w._b_bo_map(w.lay(rid), {}))
    except wz.WizardError as e:
        assert "VẪN còn dòng TABLE" in str(e), str(e)
    else:
        raise AssertionError("bo MAP khi Extract con TABLE")
    assert "MAP KT_BSH.B" in (tmp / "dirprm" / "rht.prm").read_text()


@test
def bo_MAP_TU_CHOI_khi_CHUA_ap_het_toi_moc():
    """Kiem LAI luc Replicat da dung: vi tri chay lai phai qua moc."""
    tmp = Path(tempfile.mkdtemp())
    _fs(tmp, ET.replace("TABLE KT_BSH.B;\n", ""), REP)
    w, con = _wizard(tmp)
    _them(con, "A")
    _them(con, "B")
    rid = w.tao("GO_BANG", stream="HT", src_table="B", who="t")
    con.execute("UPDATE wizard_run SET ctx_json=? WHERE id=?",
                ('{"moc_go": {"scn": 500, "file": "/x/ht000000007", "seqno": 7, "size": 9000}}',
                 rid))
    goc = ggmod.detail

    async def detail(cfg, p):
        return {"low_wm": "400", "high_wm": "450", "read_file": "/x/ht000000007", "rba": "8000"}
    ggmod.detail = detail
    try:
        asyncio.run(w._b_bo_map(w.lay(rid), {}))
    except wz.WizardError as e:
        assert "chưa áp hết" in str(e), str(e)
    else:
        raise AssertionError("bo MAP khi Replicat chua ap het toi moc")
    finally:
        ggmod.detail = goc


@test
def da_doc_qua_moc_so_sanh_dung_seqno_roi_rba():
    w, _ = _wizard()
    moc = {"seqno": 7, "size": 9000}
    f = lambda so, rba: w._da_doc_qua_moc({"read_file": f"/x/ht{so:09d}", "rba": str(rba)}, moc)
    assert f(7, 9000) and f(7, 9500) and f(8, 10)
    assert not f(7, 8999) and not f(6, 99999)
    assert not w._da_doc_qua_moc({"read_file": None, "rba": None}, moc)


@test
def ghi_moc_lay_DUNG_file_trail_moi_nhat_cua_luong():
    """Thu muc dirdat chung cho moi luong: khong duoc lay nham trail 'hta*'
    hay file .bak cua luong khac."""
    tmp = Path(tempfile.mkdtemp())
    _fs(tmp, ET, REP)
    for ten, n in (("ht000000006", 10), ("ht000000007", 2611), ("pm000000099", 5),
                   ("hta00000009", 7), ("ht000000007.bak", 1)):
        (tmp / "dirdat" / ten).write_bytes(b"x" * n)
    w, con = _wizard(tmp)
    _them(con, "A")
    _them(con, "B")
    rid = w.tao("GO_BANG", stream="HT", src_table="B", who="t")
    goc = (ggmod.status_of, ggmod.detail)

    class _St:
        is_running = False
        status = "STOPPED"

    async def status_of(cfg, p):
        return _St()

    async def detail(cfg, p):
        return {"read_scn": "381728182"}
    ggmod.status_of, ggmod.detail = status_of, detail
    try:
        r = asyncio.run(w._b_ghi_moc_trail(w.lay(rid), {}))
    finally:
        ggmod.status_of, ggmod.detail = goc
    moc = w._moc_go(w.lay(rid))
    assert moc == {"scn": 381728182, "file": str(tmp / "dirdat" / "ht000000007"),
                   "seqno": 7, "size": 2611}, (moc, r)


@test
def kiem_tra_go_TU_CHOI_bang_cuoi_cung_theo_file_tham_so_THAT():
    tmp = Path(tempfile.mkdtemp())
    _fs(tmp, "EXTRACT ETHT\nTABLE KT_BSH.B;\n", REP)
    w, con = _wizard(tmp)
    _them(con, "A")
    _them(con, "B")
    rid = w.tao("GO_BANG", stream="HT", src_table="B", who="t")
    goc = ggmod.count_param_entries
    ggmod.count_param_entries = lambda cfg, p, k: 1
    try:
        asyncio.run(w._b_kiem_tra_go(w.lay(rid), {}))
    except wz.WizardError as e:
        assert "CUỐI CÙNG" in str(e), str(e)
    else:
        raise AssertionError("buoc 1 cho go bang cuoi cung")
    finally:
        ggmod.count_param_entries = goc


@test
def param_guard_tay_VAN_chan_giam_dong_chi_GO_BANG_moi_mo():
    et2 = pm.bo_dong_table(ET, "KT_BSH", "B")
    assert pm.guard(ET, et2, process="ETHT"), "trinh sua tay phai bi chan"
    assert pm.guard(ET, et2, process="ETHT", cho_phep_bo=("KT_BSH", "B")) == []
    assert pm.guard(ET, et2, process="ETHT", cho_phep_bo=("KT_BSH", "A"))
    # Bo HAI dong mot luc thi ke ca GO_BANG cung bi chan
    et3 = pm.bo_dong_table(et2, "KT_BSH", "A")
    assert pm.guard(ET, et3, process="ETHT", cho_phep_bo=("KT_BSH", "B"))


# =============================================== ADD_TABLE tu dang ky bang
class _OraGia:
    def __init__(self, co_o_nguon: bool):
        self.co = co_o_nguon

    async def scalar(self, sql, **b):
        return 1 if self.co else 0


@test
def them_bang_tu_DANG_KY_bang_chua_co_vao_dung_luong():
    w, con = _wizard(ora=_OraGia(True))
    rid = w.tao("ADD_TABLE", stream="HT", src_table="BH_BT_NTBA_TT_PT", who="t")
    r = asyncio.run(w._b_dang_ky_bang(w.lay(rid), {}))
    m = con.execute("SELECT stream,in_extract,has_map,note FROM managed_table"
                    " WHERE src_table='BH_BT_NTBA_TT_PT'").fetchone()
    assert m["stream"] == "HT" and not m["in_extract"] and not m["has_map"], dict(m)
    assert m["note"], "note PHAI co - registry.sync() danh dau lai dong note NULL"
    assert "Đã đăng ký 1" in r["msg"], r
    # chay lai: khong ghi lan hai
    r2 = asyncio.run(w._b_dang_ky_bang(w.lay(rid), {}))
    assert "Mọi bảng đã có" in r2["msg"], r2


@test
def them_bang_TU_CHOI_bang_khong_co_o_nguon():
    w, con = _wizard(ora=_OraGia(False))
    rid = w.tao("ADD_TABLE", stream="HT", src_table="KHONG_CO_THAT", who="t")
    try:
        asyncio.run(w._b_dang_ky_bang(w.lay(rid), {}))
    except wz.WizardError as e:
        assert "KHÔNG tồn tại ở nguồn" in str(e), str(e)
    else:
        raise AssertionError("dang ky bang khong co o nguon")
    assert con.execute("SELECT COUNT(*) FROM managed_table").fetchone()[0] == 0


@test
def them_bang_TU_CHOI_bang_thuoc_luong_KHAC():
    w, con = _wizard(ora=_OraGia(True))
    _them(con, "X", stream="PM", in_extract=0, has_map=0)
    try:
        w.tao("ADD_TABLE", stream="HT", src_table="X", who="t")
    except wz.WizardError as e:
        assert "thuộc luồng PM" in str(e), str(e)
    else:
        raise AssertionError("mo phien them bang vao luong khac luong da ghi")


@test
def them_bang_LO_dang_ky_ca_lo_trong_mot_buoc():
    w, con = _wizard(ora=_OraGia(True))
    _them(con, "DA_CO", stream=None, in_extract=0, has_map=0, prepared=0)
    rid = w.tao("ADD_TABLE", stream="HT", src_table="MOI_1, MOI_2, DA_CO", who="t")
    asyncio.run(w._b_dang_ky_bang(w.lay(rid), {}))
    ds = {r["src_table"]: r["stream"] for r in con.execute(
        "SELECT src_table, stream FROM managed_table")}
    assert ds == {"MOI_1": "HT", "MOI_2": "HT", "DA_CO": "HT"}, ds


# ============================================================== migration
@test
def migration_CHECK_kind_tren_DUNG_dang_DDL_cua_server():
    """Sau lan doi ten 2026-10-15 SQLite luu `CREATE TABLE "wizard_run" (`
    CO ngoac kep. Ban dau cua migration nay so khop chuoi khong ngoac -> app
    se khong khoi dong duoc sau deploy. Test dung lai dung dang do."""
    d = Path(tempfile.mkdtemp()) / "server.db"
    con = sqlite3.connect(d)
    con.executescript(dbmod.SCHEMA.replace(",'GO_BANG'", "").replace(",'LUONG_MOI'", ""))
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='wizard_run'").fetchone()[0]
    con.execute(ddl.replace("CREATE TABLE wizard_run", "CREATE TABLE wizard_run_moi", 1)
                .replace("'NEW_STREAM'", "'NEW_STREAM','LUONG_MOI'"))
    con.execute("DROP TABLE wizard_run")
    con.execute("ALTER TABLE wizard_run_moi RENAME TO wizard_run")
    con.execute("INSERT INTO wizard_run(kind,state,src_table,created_at,created_by,"
                "updated_at,cur_step) VALUES ('LUONG_MOI','DONE','X',1,'t',1,30)")
    con.commit()
    assert '"wizard_run"' in con.execute(
        "SELECT sql FROM sqlite_master WHERE name='wizard_run'").fetchone()[0]
    con.close()
    con = dbmod.init(d)
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='wizard_run'").fetchone()[0]
    assert all(f"'{k}'" in sql for k in dbmod.KIND_HOP_LE), sql
    assert con.execute("SELECT kind, src_table FROM wizard_run").fetchone()[:] == ("LUONG_MOI", "X")
    con.execute("INSERT INTO wizard_run(kind,state,src_table,created_at,created_by,"
                "updated_at,cur_step) VALUES ('GO_BANG','OPEN','Y',1,'t',1,1)")
    try:
        con.execute("INSERT INTO wizard_run(kind,state,src_table,created_at,created_by,"
                    "updated_at,cur_step) VALUES ('BAY_BA','OPEN','Z',1,'t',1,1)")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("CHECK khong con chan loai la")
    con.close()
    dbmod.init(d).close()                  # chay lai: idempotent
