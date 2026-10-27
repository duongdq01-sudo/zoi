"""Test LUONG LOG: dich <TEN>_LOG, moi thao tac thanh mot dong INSERT.

Khoa: thu tu buoc, tham so Extract/Replicat (LOGALLSUPCOLS + UPDATERECORDFORMAT
FULL + INSERTALLRECORDS), DDL bang log (khong khoa, cot NULL), tach danh sach
quan ly (mot bang vua thuong vua log), va cac chot chan tron luong.
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
from app import ddl                                           # noqa: E402
from app import registry                                      # noqa: E402
from app import toml_ghi                                      # noqa: E402
from app import wizard as wz                                  # noqa: E402
from app.adapters import gg as ggmod                          # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _so(kind, ten):
    return wz.so_buoc(kind, ten)


class _Cfg:
    def __init__(self, tmp: Path | None = None):
        self.gg_home = tmp or Path("/u01/app/ogg")
        self.streams = {
            "HT": {"extract": "ETHT", "replicat": "RHT", "trail": "ht"},
            "LOG1": {"extract": "ETLOG1", "replicat": "RLOG1", "trail": "la", "loai": "log"},
        }

    def param_file(self, ten):
        return self.gg_home / "dirprm" / f"{ten.lower()}.prm"


def _wizard(tmp=None, ora=None):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(dbmod.SCHEMA)
    return wz.Wizard(_Cfg(tmp), con, ora, None, None), con


def _them(con, bang, ten, **kw):
    d = dict(src_owner="KT_BSH", src_table=ten, tgt_owner="ODS",
             tgt_table=ten + ("_LOG" if bang == "managed_log" else ""),
             stream="HT", prepared=1, in_extract=1, has_map=1)
    d.update(kw)
    con.execute(
        f"INSERT INTO {bang}(src_owner,src_table,tgt_owner,tgt_table,stream,prepared,"
        "in_extract,has_map) VALUES (:src_owner,:src_table,:tgt_owner,:tgt_table,"
        ":stream,:prepared,:in_extract,:has_map)", d)


# ================================================================ thu tu
@test
def luong_log_KHONG_co_nap_chong_trung_doi_chieu():
    """Log bat dau tu luc vao luong: khong nap ban dau, khong HANDLECOLLISIONS,
    khong doi chieu so dong (bang log luon nhieu dong hon nguon)."""
    for kind in ("LUONG_LOG", "THEM_LOG"):
        ten = {s.name for s in wz.BO_BUOC[kind]}
        for xau in ("NAP", "SCN_FLOOR", "SINH_SCRIPT_NAP", "INDEX_UNUSABLE",
                    "BO_HANDLECOLLISIONS", "THEM_HANDLECOLLISIONS", "VERIFY", "CHO_LOW_WM"):
            assert xau not in ten, (kind, xau)


@test
def thu_tu_LUONG_LOG_khong_duoc_dao():
    k = "LUONG_LOG"
    assert _so(k, "DANG_KY_BANG") < _so(k, "TAO_BANG_DICH") < _so(k, "KIEM_BANG_DICH")
    # bang dich + quyen + TRANDATA phai xong TRUOC khi cham DB nguon (dang ky capture)
    assert _so(k, "KIEM_BANG_DICH") < _so(k, "REGISTER_EXTRACT")
    assert _so(k, "ADD_TRANDATA") < _so(k, "REGISTER_EXTRACT")
    assert _so(k, "TAO_PARAM") < _so(k, "REGISTER_EXTRACT") < _so(k, "ADD_EXTRACT")
    assert _so(k, "ADD_CHECKPOINTTABLE") < _so(k, "ADD_REPLICAT")
    assert _so(k, "ADD_REPLICAT") < _so(k, "DANG_KY_CAU_HINH") < _so(k, "START_EXTRACT")


@test
def thu_tu_THEM_LOG_MAP_truoc_TABLE():
    k = "THEM_LOG"
    assert (_so(k, "STOP_REPLICAT") < _so(k, "THEM_MAP") < _so(k, "STOP_EXTRACT")
            < _so(k, "THEM_TABLE") < _so(k, "START_EXTRACT") < _so(k, "START_REPLICAT"))


@test
def GO_LOG_cung_thu_tu_voi_GO_BANG():
    assert [(s.no, s.name) for s in wz.GO_LOG] == [(s.no, s.name) for s in wz.GO_BANG]


@test
def moi_buoc_log_deu_co_handler():
    for kind in wz.KIND_LOG:
        tt = wz.TIEN_TO.get(kind, "")
        for s in wz.BO_BUOC[kind]:
            fn = (getattr(wz.Wizard, f"_b_{tt}{s.name.lower()}", None)
                  or getattr(wz.Wizard, f"_b_{s.name.lower()}", None))
            assert fn is not None, (kind, s.name)


@test
def luong_log_buoc_chay_replicat_KHONG_dinh_vao_handler_ns():
    """Sau DANG_KY_CAU_HINH luong da co trong cfg -> chay qua control (co ghi
    su kien), khong goi thang gg.start_process."""
    assert wz.Wizard._b_start_replicat_2 is wz.Wizard._b_start_replicat
    assert getattr(wz.Wizard, "_b_ns_start_replicat_2", None) is None


# =========================================================== tham so
def _run_log():
    return {"kind": "LUONG_LOG", "id": 1, "created_extract": "ETLOG1",
            "created_replicat": "RLOG1", "created_trail_prefix": "la",
            "created_ckpt_table": "OGG_BSH.RLOG1", "src_owner": "KT_BSH",
            "src_table": "BH_BT_NTBA_TT_PT", "tgt_owner": "ODS",
            "tgt_table": "BH_BT_NTBA_TT_PT_LOG", "keycols": None}


@test
def extract_log_co_LOGALLSUPCOLS_va_UPDATERECORDFORMAT_FULL_va_KHONG_DDL():
    w, _ = _wizard()
    prm = w._prm_extract(_run_log())
    dong = [l.strip().upper() for l in prm.splitlines()]
    assert "LOGALLSUPCOLS" in dong
    assert "UPDATERECORDFORMAT FULL" in dong, "INSERTALLRECORDS chi chay voi FULL"
    assert not any(l.startswith("DDL") for l in dong), "TRUNCATE/DROP khong duoc lan"
    assert f"EXTTRAIL {Path('/u01/app/ogg')}/dirdat/la".upper() in dong
    assert "TABLE KT_BSH.BH_BT_NTBA_TT_PT;" in dong


@test
def replicat_log_INSERTALLRECORDS_khong_chong_trung_khong_bo_loi():
    w, _ = _wizard()
    prm = w._prm_replicat(_run_log())
    u = prm.upper()
    assert "INSERTALLRECORDS" in u and "IGNOREUPDATEBEFORES" in u
    assert "HANDLECOLLISIONS" not in u
    assert "REPERROR" not in u, "bo im lang mot dong lich su te hon ABEND"
    assert "TARGET ODS.BH_BT_NTBA_TT_PT_LOG" in u
    assert "LOG_TYPE = @CASE (@GETENV ('GGHEADER', 'OPTYPE'), 'INSERT', 'I', 'DELETE', 'D', 'U')" in prm
    assert "COMMIT_TS = @GETENV ('GGHEADER', 'COMMITTIMESTAMP')" in prm
    # GG 21.3 doc param theo ANSI SQL: nhay kep la TEN COT -> RLOG abend
    # OGG-00919 'could not find column "GGHEADER"' (2026-10-27)
    assert '"' not in prm[prm.upper().index("COLMAP"):], "chuoi hang trong COLMAP phai nhay don"
    # bang _LOG khong khoa -> GG lay moi cot lam khoa; thieu LOG_TIME trong
    # COLMAP la abend OGG-00918 (2026-10-27)
    assert "LOG_TIME = @DATENOW ()" in prm


@test
def luong_thuong_KHONG_bi_doi_tham_so():
    w, _ = _wizard()
    r = dict(_run_log(), kind="LUONG_MOI", tgt_table="BH_BT_NTBA_TT_PT", keycols="A")
    assert "UPDATERECORDFORMAT" not in w._prm_extract(r).upper()
    assert "INSERTALLRECORDS" not in w._prm_replicat(r).upper()
    assert "HANDLECOLLISIONS" in w._prm_replicat(r).upper()


# ================================================================== DDL
_COLS = [{"column_name": "MA_DVI", "data_type": "VARCHAR2", "data_length": 20, "char_length": 20,
          "char_used": "C", "data_precision": None, "data_scale": None, "nullable": "N",
          "column_id": 1},
         {"column_name": "SO_TIEN", "data_type": "NUMBER", "data_length": 22, "char_length": 0,
          "char_used": None, "data_precision": 18, "data_scale": 2, "nullable": "Y",
          "column_id": 2}]


@test
def ddl_log_KHONG_khoa_cot_nguon_deu_NULL_co_3_cot_log():
    s = ddl.sinh_ddl_log(tgt_owner="ODS", tgt_table="A_LOG", cols=_COLS, src="KT_BSH.A")
    u = s.upper()
    assert "PRIMARY KEY" not in u and "UNIQUE" not in u
    tao = u[u.index("CREATE TABLE"):u.index(") TABLESPACE")]
    assert '"MA_DVI" VARCHAR2(20 CHAR)' in tao and '"MA_DVI" VARCHAR2(20 CHAR) NOT NULL' not in tao
    for c in ('"LOG_TYPE" VARCHAR2(1)', '"COMMIT_TS" TIMESTAMP(6)',
              '"LOG_TIME" DATE DEFAULT SYSDATE NOT NULL'):
        assert c in tao, c
    assert "GRANT SELECT ON ODS.\"A_LOG\" TO GGADM_RO;" in s


@test
def ddl_log_TU_CHOI_khi_nguon_trung_ten_cot_log():
    cols = _COLS + [dict(_COLS[1], column_name="LOG_TIME", column_id=3)]
    try:
        ddl.sinh_ddl_log(tgt_owner="ODS", tgt_table="A_LOG", cols=cols, src="KT_BSH.A")
    except ddl.DdlError as e:
        assert "LOG_TIME" in str(e)
    else:
        raise AssertionError("tao duoc bang log trung ten cot")


# ======================================================= tron luong / tao
@test
def quy_trinh_log_TU_CHOI_luong_thuong_va_nguoc_lai():
    w, con = _wizard()
    _them(con, "managed_log", "A", stream="LOG1")
    _them(con, "managed_log", "B", stream="LOG1")
    _them(con, "managed_table", "C")
    _them(con, "managed_table", "D")
    for kind, luong, bang in (("THEM_LOG", "HT", "X"), ("GO_LOG", "HT", "A"),
                              ("ADD_TABLE", "LOG1", "C"), ("GO_BANG", "LOG1", "C")):
        try:
            w.tao(kind, stream=luong, src_table=bang, who="t")
        except wz.WizardError as e:
            assert "luồng" in str(e).lower(), (kind, str(e))
            continue
        raise AssertionError(f"{kind} chay duoc tren luong {luong}")


@test
def mot_bang_VUA_thuong_VUA_mo_duoc_luong_log():
    """Quyet dinh cua nguoi dung: ODS.A va ODS.A_LOG cung ton tai."""
    w, con = _wizard()
    _them(con, "managed_table", "BH_BT_NTBA_TT_PT")          # dang dong bo thuong o HT
    rid = w.tao("LUONG_LOG", stream=None, src_table="BH_BT_NTBA_TT_PT", who="t")
    r = w.lay(rid)
    assert r["tgt_table"] == "BH_BT_NTBA_TT_PT_LOG" and r["stream"] is None, dict(r)
    m = con.execute("SELECT stream FROM managed_table WHERE src_table='BH_BT_NTBA_TT_PT'").fetchone()
    assert m["stream"] == "HT", "danh sach thuong KHONG duoc bi dong vao"


@test
def ten_bang_log_qua_30_ky_tu_bi_tu_choi():
    w, _ = _wizard()
    try:
        w.tao("LUONG_LOG", stream=None, src_table="A" * 27, who="t")
    except wz.WizardError as e:
        assert "30 ký tự" in str(e), str(e)
    else:
        raise AssertionError("ten _LOG vuot 30 ky tu ma van mo duoc")


@test
def khoa_doc_quyen_tao_luong_GOP_luong_moi_va_luong_log():
    """Ca hai deu dang ky capture vao DB nguon -> toi da MOT phien cung luc."""
    w, con = _wizard()
    w.tao("LUONG_MOI", stream=None, src_table="X1", who="t")
    try:
        w.tao("LUONG_LOG", stream=None, src_table="X2", who="t")
    except wz.WizardError as e:
        assert "Tối đa MỘT phiên" in str(e), str(e)
    else:
        raise AssertionError("mo duoc hai phien tao luong cung luc")


@test
def auto_kiem_dieu_kien_cac_quy_trinh_log():
    _, con = _wizard()
    _them(con, "managed_log", "A", stream="LOG1")
    _them(con, "managed_log", "CHI_MOT", stream="LOG2")
    # bang moi tinh: tao luong log va them vao luong log deu duoc
    assert auto.kiem_dieu_kien(con, "LUONG_LOG", "MOI")["tgt_table"] == "MOI_LOG"
    assert auto.kiem_dieu_kien(con, "THEM_LOG", "MOI")["stream"] is None
    for kind, bang, chu in (("THEM_LOG", "A", "ĐÃ ghi log"), ("LUONG_LOG", "A", "đã thuộc luồng log"),
                            ("GO_LOG", "CHI_MOT", "CUỐI CÙNG"), ("GO_LOG", "KHONG_CO", "danh sách log")):
        try:
            auto.kiem_dieu_kien(con, kind, bang)
        except auto.AutoError as e:
            assert chu in str(e), (kind, bang, str(e))
            continue
        raise AssertionError(f"{kind} {bang} khong bi tu choi")


# ==================================================== danh sach quan ly
class _OraGia:
    def __init__(self, allcols_truoc=False, allcols_sau=True, co_o_nguon=True):
        self.lan = 0
        self.truoc, self.sau, self.co = allcols_truoc, allcols_sau, co_o_nguon

    async def scalar(self, sql, **b):
        if "dba_log_groups" in sql:
            self.lan += 1
            return 1 if (self.truoc if self.lan == 1 else self.sau) else 0
        return 1 if self.co else 0

    async def rows(self, sql, **b):
        return []


@test
def dang_ky_log_ghi_managed_log_KHONG_dung_managed_table():
    w, con = _wizard(ora=_OraGia())
    rid = w.tao("LUONG_LOG", stream=None, src_table="X", who="t")
    asyncio.run(w._b_ns_dang_ky_bang(w.lay(rid), {}))
    m = con.execute("SELECT tgt_table, stream FROM managed_log WHERE src_table='X'").fetchone()
    assert m["tgt_table"] == "X_LOG" and m["stream"] is None, dict(m)
    assert con.execute("SELECT COUNT(*) FROM managed_table").fetchone()[0] == 0


@test
def trandata_log_DUNG_LAI_va_in_lenh_ALTER_khi_ALLCOLS_chua_co():
    """Bang da co TRANDATA thuong: `add trandata allcols` co the khong them
    nhom ALL COLUMN. Cong cu KHONG tat TRANDATA cu - in lenh cho DBA."""
    w, con = _wizard(ora=_OraGia(allcols_truoc=False, allcols_sau=False))
    rid = w.tao("LUONG_LOG", stream=None, src_table="X", who="t")
    goc = ggmod.add_trandata

    async def add(cfg, o, t, cols=None, *, allcols=False):
        assert allcols, "luong log phai bat ALLCOLS"
        return True, ["ok"]
    ggmod.add_trandata = add
    try:
        asyncio.run(w._b_add_trandata(w.lay(rid), {}))
    except wz.WizardError as e:
        assert "ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS" in str(e), str(e)
    else:
        raise AssertionError("cho qua khi nguon chua co ALL COLUMN LOGGING")
    finally:
        ggmod.add_trandata = goc


@test
def trandata_log_da_co_ALLCOLS_thi_khong_lam_lai():
    w, con = _wizard(ora=_OraGia(allcols_truoc=True))
    rid = w.tao("LUONG_LOG", stream=None, src_table="X", who="t")
    asyncio.run(w._b_ns_dang_ky_bang(w.lay(rid), {}))
    r = asyncio.run(w._b_add_trandata(w.lay(rid), {}))
    assert "không làm lại" in r["msg"], r
    assert con.execute("SELECT prepared FROM managed_log WHERE src_table='X'").fetchone()[0] == 1


@test
def registry_sync_tach_luong_thuong_va_luong_log():
    """Bang A trong CA luong thuong HT lan luong log LOG1: managed_table ghi
    HT (dich ODS.A), managed_log ghi LOG1 (dich ODS.A_LOG) - khong de nhau."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "dirprm").mkdir()
    (tmp / "dirprm" / "etht.prm").write_text("EXTRACT ETHT\nTABLE KT_BSH.A;\nTABLE KT_BSH.B;\n")
    (tmp / "dirprm" / "rht.prm").write_text(
        "REPLICAT RHT\nMAP KT_BSH.A, TARGET ODS.A, COLMAP (USEDEFAULTS);\n"
        "MAP KT_BSH.B, TARGET ODS.B, COLMAP (USEDEFAULTS);\n")
    (tmp / "dirprm" / "etlog1.prm").write_text("EXTRACT ETLOG1\nTABLE KT_BSH.A;\n")
    (tmp / "dirprm" / "rlog1.prm").write_text(
        "REPLICAT RLOG1\nMAP KT_BSH.A, TARGET ODS.A_LOG, INSERTALLRECORDS, COLMAP (USEDEFAULTS);\n")
    w, con = _wizard(tmp, ora=_OraGia())
    asyncio.run(registry.sync(w.cfg, con, _OraGia()))
    t = {r["src_table"]: (r["stream"], r["tgt_table"]) for r in con.execute(
        "SELECT src_table, stream, tgt_table FROM managed_table")}
    lg = {r["src_table"]: (r["stream"], r["tgt_table"], r["in_extract"], r["has_map"])
          for r in con.execute("SELECT * FROM managed_log")}
    assert t == {"A": ("HT", "A"), "B": ("HT", "B")}, t
    assert lg == {"A": ("LOG1", "A_LOG", 1, 1)}, lg


@test
def kiem_bang_log_TU_CHOI_khoa_chinh_va_cot_NOT_NULL():
    class Ora(_OraGia):
        def __init__(self, pk, nn):
            super().__init__()
            self.pk, self.nn = pk, nn

        async def scalar(self, sql, **b):
            if "dba_constraints" in sql:
                return 1 if self.pk else 0
            if "dba_indexes" in sql:
                return 0
            return await super().scalar(sql, **b)

        async def rows(self, sql, **b):
            return [{"column_name": "MA_DVI", "nullable": "N" if self.nn else "Y"},
                    {"column_name": "LOG_TYPE", "nullable": "Y"},
                    {"column_name": "COMMIT_TS", "nullable": "Y"},
                    {"column_name": "LOG_TIME", "nullable": "N"}]
    run = _run_log()
    for pk, nn, chu in ((True, False, "ABEND"), (False, True, "NOT NULL")):
        w, _ = _wizard(ora=Ora(pk, nn))
        try:
            asyncio.run(w._kiem_bang_log(run, "BH_BT_NTBA_TT_PT_LOG"))
        except wz.WizardError as e:
            assert chu in str(e), (pk, nn, str(e))
            continue
        raise AssertionError(f"cho qua bang log pk={pk} nn={nn}")
    w, _ = _wizard(ora=Ora(False, False))
    assert asyncio.run(w._kiem_bang_log(run, "BH_BT_NTBA_TT_PT_LOG"))


@test
def go_log_ha_co_o_managed_log_KHONG_dung_managed_table():
    w, con = _wizard()
    _them(con, "managed_table", "A")                          # A cung dong bo thuong
    _them(con, "managed_log", "A", stream="LOG1")
    run = {"kind": "GO_LOG", "src_table": "A", "stream": "LOG1", "id": 9}
    con.execute(f"UPDATE {w._bang_qly(run)} SET in_extract=0 WHERE src_table='A'")
    assert con.execute("SELECT in_extract FROM managed_log WHERE src_table='A'").fetchone()[0] == 0
    assert con.execute("SELECT in_extract FROM managed_table WHERE src_table='A'").fetchone()[0] == 1


@test
def toml_khoi_luong_log_co_loai_va_dang_ky_giu_loai():
    k = toml_ghi.khoi_streams("LOG1", "ETLOG1", "RLOG1", "la", "Log", loai="log")
    assert 'loai     = "log"' in k
    assert "loai" not in toml_ghi.khoi_streams("PM2", "ETPM2", "RPM2", "pb", "x")
    tmp = Path(tempfile.mkdtemp()) / "app.toml"
    tmp.write_text('[streams.HT]\nextract  = "ETHT"\nreplicat = "RHT"\ntrail    = "ht"\n'
                   'ten      = "x"\n', encoding="utf-8")

    class Cfg:
        app_toml = tmp
        streams = {"HT": {"extract": "ETHT", "replicat": "RHT", "trail": "ht"}}
    goc = ggmod.dang_ky_process
    ggmod.dang_ky_process = lambda *a: None
    try:
        toml_ghi.dang_ky_luong(Cfg, "LOG1", "ETLOG1", "RLOG1", "la", "Log", loai="log")
    finally:
        ggmod.dang_ky_process = goc
    assert Cfg.streams["LOG1"]["loai"] == "log"
    import tomllib
    assert tomllib.loads(tmp.read_text(encoding="utf-8"))["streams"]["LOG1"]["loai"] == "log"


@test
def trang_bang_hien_ca_bang_cua_luong_log():
    """Nut loc LOG o trang Bang tung luon rong vi listing() chi doc
    managed_table (2026-10-27)."""
    _, con = _wizard()
    _them(con, "managed_table", "BH_HD_GOC_TTPT", stream="PMNV3")
    _them(con, "managed_log", "BH_HD_GOC_TTPT", stream="LOG")
    log = registry.listing(con, "LOG")
    assert [(r["src_table"], r["tgt_table"]) for r in log] == \
        [("BH_HD_GOC_TTPT", "BH_HD_GOC_TTPT_LOG")], log
    assert log[0]["la_log"] is True
    # tables.html chay StrictUndefined: thieu khoa la trang 500
    for k in ("src_rows", "tgt_rows", "v_ts", "v_matched", "v_mode"):
        assert k in log[0], k
    assert [r["stream"] for r in registry.listing(con, "PMNV3")] == ["PMNV3"]
    assert sorted(r["stream"] for r in registry.listing(con)) == ["LOG", "PMNV3"]
