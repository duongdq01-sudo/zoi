"""Test LO NHIEU BANG cua wizard ADD_TABLE.

VI SAO TINH NANG NAY TON TAI
Lo CN_* (thang 10/2026) co CN_CH la dong gom va CN_CT / CN_PS / CN_TT la
dong chi tiet, dung chung khoa (MA_DVI, SO_ID). Chay tam phien ADD_TABLE
rieng nghia la tam `AS OF SCN` KHAC NHAU, va trong khoang giua cac anh chup
o dich se co dong chi tiet khong co dong goc. Nen ca lo phai nap o DUNG MOT
SCN.

CAI GI DUOC KHOA O DAY
  1. `tach_ten_bang` tach dung moi kieu nguoi dung go
  2. Script nap ca lo dung DUNG MOT bien SCN cho moi bang
  3. Lop chan 1 (dich phai RONG) kiem HET cac bang TRUOC khi nap bang dau
  4. Uoc thoi gian nap tinh theo TONG so dong, khong phai mot bang
  5. Do song song tinh RIENG theo tung bang
  6. Buoc chay qua JOB khong duoc nam trong MOI_BANG
  7. `_ghi_run` chuyen huong ctx_json/keycols vao ctx["bang"][ten]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import wizard as wz                                    # noqa: E402

TESTS = []



def _ghep(src):
    """Ghep cac literal NOI TIEP trong source thanh mot chuoi lien.

    Can thiet vi mot cau dai duoc ngat thanh nhieu literal:
        "Phải nhập cột "
        "khoá."
    Tim chuoi con tren source THO se khong khop, va test se bao hong OAN
    (da xay ra that khi dich cac thong diep sang tieng Viet).
    """
    import re
    return re.sub(r'"\s+"', "", src)


def test(fn):
    TESTS.append(fn)
    return fn


# Lo that dung de dung test: ten + so dong do duoc o nguon 2026-10-14
LO = [("CN_CH", 1294304), ("CN_CT", 1516157), ("CN_MA_KH", 73350),
      ("CN_MA_SONO", 283), ("CN_PS", 885711), ("CN_TT", 1165938),
      ("XD_MA_CTR", 0), ("XL_MA_CTR", 0)]
TONG = sum(n for _, n in LO)


class ConGia:
    """Thay cho sqlite3.Connection: chi tra ten bang dich = ten bang nguon."""

    def __init__(self, ds):
        self.ds = ds

    def execute(self, sql, args=()):
        ten = args[0] if args else None
        if "managed_table" in sql and ten in self.ds:
            return _Cur({"src_owner": "KT_BSH", "tgt_owner": "ODS",
                         "tgt_table": ten, "tgt_table_": ten,
                         "keycols": "MA_DVI, SO_ID"})
        return _Cur(None)


class _Cur:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class WGia(wz.Wizard):
    """Wizard that, nhung khong co DB/ora/gg - chi de goi cac ham sinh SQL."""

    def __init__(self, ds, n_theo_bang):
        self.con = ConGia(ds)
        self._ds = ds
        self._n = n_theo_bang

    def lay(self, rid):
        return self._run

    def _dat(self, run):
        self._run = run
        return run


def _run_lo(ds=None, floor=163406205):
    ds = ds or [b for b, _ in LO]
    ctx = {"bang": {b: {"n_src": n} for b, n in LO if b in ds}}
    return {"id": 42, "kind": "ADD_TABLE", "stream": "HT",
            "src_owner": "KT_BSH", "src_table": ds[0],
            "src_tables": json.dumps(ds),
            "tgt_owner": "ODS", "tgt_table": ds[0],
            "keycols": "MA_DVI, SO_ID", "scn_floor": floor,
            "ctx_json": json.dumps(ctx)}


# ===================================================== 1. tach ten bang
@test
def tach_ten_bang_moi_kieu_nguoi_dung_go():
    f = wz.tach_ten_bang
    assert f("CN_CH") == ["CN_CH"]
    assert f("CN_CH,CN_CT") == ["CN_CH", "CN_CT"]
    assert f("CN_CH, CN_CT , CN_PS") == ["CN_CH", "CN_CT", "CN_PS"]
    assert f("CN_CH\nCN_CT\r\nCN_PS") == ["CN_CH", "CN_CT", "CN_PS"]
    assert f("cn_ch cn_ct") == ["CN_CH", "CN_CT"], "phai viet hoa"
    assert f("CN_CH;CN_CT") == ["CN_CH", "CN_CT"]
    assert f("  ") == []
    assert f(None) == []
    # Giu NGUYEN THU TU: bang dau tien la ten hien tren moi cho
    assert f("XL_MA_CTR, CN_CH")[0] == "XL_MA_CTR"


# ============================================ 2. mot SCN cho ca lo
@test
def script_nap_lo_dung_DUNG_MOT_SCN_cho_moi_bang():
    """Day la ly do ky thuat cua ca tinh nang lo.

    Moi bang phai doc `AS OF SCN ' || v_scn` - CUNG mot bien, gan DUNG MOT
    LAN o dau script. Hai lan gan `SELECT current_scn` la hai anh chup khac
    nhau, va the la mat dung cai tinh nhat quan dang di tim.
    """
    w = WGia([b for b, _ in LO], dict(LO))
    sql = w._sql_nap_lo(_run_lo(), [b for b, _ in LO])

    # Chi MOT lan doc current_scn
    assert sql.count("SELECT current_scn INTO v_scn") == 1, sql

    # Moi bang phai dung `AS OF SCN ' || v_scn`
    for b, _ in LO:
        assert 'FROM KT_BSH."%s"@DBL_PMNV AS OF SCN ' % b in sql, b
    # 8 bang x 2 duong (APPEND + conventional) = 16 lan dung v_scn de doc
    assert sql.count("AS OF SCN ' || v_scn") == len(LO) * 2, \
        sql.count("AS OF SCN ' || v_scn")

    # Khong duoc co lan doc SCN nao khac
    assert sql.count("current_scn") == 1, sql


@test
def script_nap_lo_in_moc_rieng_cho_tung_bang():
    w = WGia([b for b, _ in LO], dict(LO))
    sql = w._sql_nap_lo(_run_lo(), [b for b, _ in LO])
    for b, _ in LO:
        assert "SO_DONG_%s=" % b in sql, b
    assert "MAXSCN=" in sql
    assert "SO_DONG=' || v_tong" in sql


# ================================ 3. lop chan 1 kiem HET truoc khi nap
@test
def lop_chan_1_kiem_HET_cac_bang_TRUOC_khi_nap_bang_dau():
    """Phat hien bang thu 6 khong rong luc da nap 5 bang la pha dung cai
    tinh nhat quan ma mot SCN duy nhat dung de bao ve."""
    w = WGia([b for b, _ in LO], dict(LO))
    sql = w._sql_nap_lo(_run_lo(), [b for b, _ in LO])

    vi_tri_kiem = [sql.index('SELECT 1 FROM ODS."%s"' % b) for b, _ in LO]
    vi_tri_nap = [sql.index('INSERT /*+ APPEND */ INTO ODS."%s"' % b)
                  for b, _ in LO]
    assert max(vi_tri_kiem) < min(vi_tri_nap), (
        "co lenh kiem RONG nam SAU mot lenh INSERT - lop chan 1 khong con "
        "la chot truoc khi nap")
    assert sql.count("-20012") == len(LO), "thieu lop chan 1 cho mot bang"


# ======================================= 4. uoc thoi gian theo TONG
@test
def uoc_thoi_gian_nap_tinh_theo_TONG_so_dong_ca_lo():
    """Anh flashback phai con hieu luc den het bang CUOI CUNG.

    Tinh theo mot bang la uoc THIEU, va uoc thieu o day dan den ORA-01555
    giua dot nap - da an that o lo 4 (nap 64 phut).
    """
    from app.wizard_index import TOC_DO_NAP_THAP
    w = WGia([b for b, _ in LO], dict(LO))
    sql = w._sql_nap_lo(_run_lo(), [b for b, _ in LO])
    uoc = int(TONG / TOC_DO_NAP_THAP)
    assert "ước thời gian nạp cả lô = %d s" % uoc in sql, sql[:400]
    assert "IF %d > v_undo THEN" % uoc in sql.replace(
        "v_undo > 0 AND ", ""), sql
    # KHONG duoc dung uoc cua rieng bang dau tien
    uoc_mot = int(LO[0][1] / TOC_DO_NAP_THAP)
    if uoc_mot != uoc:
        assert "ước thời gian nạp cả lô = %d s" % uoc_mot not in sql


# ================================ 5. do song song rieng theo bang
@test
def do_song_song_tinh_RIENG_theo_tung_bang():
    """Index cua bang 283 dong khong duoc an do song song cua bang 1,5
    trieu dong chi vi dung chung mot script."""
    from app.wizard_index import IndexMixin

    class W(wz.Wizard):
        def __init__(self):
            self.con = ConGia([b for b, _ in LO])

    w = W()
    run = _run_lo()
    cua_bang = {"CN_CT_I1": "CN_CT", "CN_MA_SONO_I1": "CN_MA_SONO"}
    sql = w._sql_index(run, ["CN_CT_I1", "CN_MA_SONO_I1"], "REBUILD",
                       cua_bang)
    # CN_CT 1.516.157 dong -> 4 ; CN_MA_SONO 283 dong -> NOPARALLEL
    assert IndexMixin.do_song_song(1516157) == 4
    assert IndexMixin.do_song_song(283) == 1
    assert 'ALTER INDEX ODS."CN_CT_I1" REBUILD TABLESPACE BSH_INDEX' \
           ' PARALLEL 4;' in sql, sql
    assert 'ALTER INDEX ODS."CN_MA_SONO_I1" REBUILD TABLESPACE BSH_INDEX' \
           ' NOPARALLEL;' in sql, sql
    # PARALLEL n thi phai NOPARALLEL ngay sau
    assert 'ALTER INDEX ODS."CN_CT_I1" NOPARALLEL;' in sql


# ================== 6. buoc qua job khong duoc nam trong MOI_BANG
@test
def buoc_chay_qua_job_KHONG_duoc_nam_trong_MOI_BANG():
    """Buoc qua job chi doi duoc MOT job. Nam trong MOI_BANG thi tu bang
    thu hai tro di job bi bo roi im lang."""
    qua_job = {"TAO_BANG_DICH", "INDEX_UNUSABLE", "NAP", "INDEX_REBUILD",
               "GATHER_STATS", "TRUNCATE"}
    cham = qua_job & wz.MOI_BANG
    assert not cham, "buoc qua job lai nam trong MOI_BANG: %s" % cham


@test
def moi_ten_trong_MOI_BANG_phai_la_buoc_that():
    ten = {s.name for s in wz.ADD_TABLE} | {s.name for s in wz.RESYNC_TABLE}
    la = wz.MOI_BANG - ten
    assert not la, "MOI_BANG co ten khong phai buoc nao: %s" % la


# ===================== 7. ghi_run chuyen huong ctx rieng cua tung bang
@test
def ghi_run_chuyen_huong_ctx_va_keycols_vao_ctx_bang():
    """Handler mot bang ghi ctx CUA RIENG NO. Ghi thang muc phien se de mat
    ket qua khao sat cua nhung bang truoc."""
    ghi = {}

    class W(wz.Wizard):
        def __init__(self):
            self._run = {"id": 1, "ctx_json": json.dumps(
                {"bang": {"CN_CH": {"n_src": 1294304}}})}

        def lay(self, rid):
            return self._run

        def _ghi_run_that(self, rid, **kw):
            ghi.update(kw)

    w = W()
    # Gia lap cau lenh UPDATE bang cach thay chinh con
    class C:
        def execute(self, sql, p):
            ghi.update(p)
    w.con = C()

    w._lo_bang = "CN_CT"
    w._ghi_run(1, ctx_json=json.dumps({"n_src": 1516157}),
               keycols="MA_DVI, SO_ID, BT")
    ctx = json.loads(ghi["ctx_json"])
    assert ctx["bang"]["CN_CH"]["n_src"] == 1294304, \
        "ctx cua bang truoc bi xoa - day dung la loi can chan"
    assert ctx["bang"]["CN_CT"]["n_src"] == 1516157
    assert ctx["bang"]["CN_CT"]["keycols"] == "MA_DVI, SO_ID, BT"
    # keycols KHONG duoc ghi muc phien
    assert "keycols" not in ghi or ghi.get("keycols") is None, ghi

    # Ngoai vong lap thi ghi thang nhu cu
    ghi.clear()
    w._lo_bang = None
    w._ghi_run(1, ctx_json=json.dumps({"x": 1}), keycols="A")
    assert json.loads(ghi["ctx_json"]) == {"x": 1}
    assert ghi["keycols"] == "A"


@test
def phien_mot_bang_van_la_list_mot_phan_tu():
    """Khong co hai duong code song song: phien mot bang la truong hop rieng
    cua lo."""
    w = WGia(["CN_CH"], dict(LO))
    r = {"src_table": "CN_CH", "src_tables": None}
    assert w._cac_bang(r) == ["CN_CH"]
    assert w._la_lo(r) is False
    r2 = {"src_table": "CN_CH", "src_tables": json.dumps(["CN_CH", "CN_CT"])}
    assert w._cac_bang(r2) == ["CN_CH", "CN_CT"]
    assert w._la_lo(r2) is True
    # JSON hong thi khong duoc no, suy ra tu src_table
    r3 = {"src_table": "CN_CH", "src_tables": "{khong phai json"}
    assert w._cac_bang(r3) == ["CN_CH"]


@test
def gioi_han_lo_la_mot_so_duong():
    assert isinstance(wz.GIOI_HAN_LO, int) and wz.GIOI_HAN_LO >= 8


# ============ 8. KEYCOLS suy tu PK that, khong bat go tay 8 lan
@test
def keycols_suy_tu_PK_that_khi_khong_nhap():
    """Bang co PK that thi PK CHINH LA cau tra loi dung.

    Bat go tay 8 lan cho 8 bang la 8 dip go sai ma khong doi lay them mot
    kiem chung nao - tinh duy nhat van duoc kiem tren DU LIEU THAT.
    """
    import asyncio
    import inspect
    src = _ghep(inspect.getsource(wz.Wizard._b_xac_nhan_keycols))
    # Nhanh suy tu PK phai co, va phai dung ctx cua KHAO_SAT
    assert 'ctx0.get("co_pk")' in src, src[:600]
    assert 'ctx0.get("pk_cols")' in src
    assert "tu_pk = True" in src
    # Va kiem tinh duy nhat tren du lieu that KHONG duoc bo
    assert "COUNT(DISTINCT" in src, "mat buoc kiem duy nhat tren du lieu that"
    assert "KHÔNG duy nhất trên dữ liệu thật" in src


@test
def khong_co_PK_thi_van_bat_nhap_keycols():
    """Bang khong co PK that thi cong cu khong duoc tu bia ra khoa."""
    import inspect
    src = _ghep(inspect.getsource(wz.Wizard._b_xac_nhan_keycols))
    assert "Phải nhập cột khoá" in src
    assert "KHÔNG có khoá chính thật nên công cụ không có gì để suy" in src


@test
def khong_PK_co_unique_index_thi_lay_cot_index():
    """Quyet dinh 2026-10-23 (lo PMNV3): chi co unique index thi dung cot cua no.

    Index theo bieu thuc bi loai (KEYCOLS chi nhan ten cot), va kiem duy
    nhat tren du lieu that van chay sau khi suy.
    """
    import inspect
    ks = _ghep(inspect.getsource(wz.Wizard._b_khao_sat))
    assert "ui_cols=ui_cols" in ks
    assert '"FUNCTION" not in' in ks, "index theo bieu thuc khong duoc lam khoa"
    assert "and not pk" in ks, "co PK thi PK thang, khong lay unique index"
    src = _ghep(inspect.getsource(wz.Wizard._b_xac_nhan_keycols))
    assert 'ctx0.get("ui_cols")' in src and "tu_ui = True" in src
    # nhanh unique index nam SAU nhanh PK
    assert src.index('ctx0.get("pk_cols")') < src.index('ctx0.get("ui_cols")')
    assert "KHÔNG duy nhất trên dữ liệu thật" in src


# ===== 9. KEYCOLS nhieu cot: KHONG duoc dung COUNT(DISTINCT a, b)
@test
def kiem_duy_nhat_khong_duoc_dung_COUNT_DISTINCT_nhieu_cot():
    """ORA-00909 da xay ra THAT ngay 2026-10-14.

    `COUNT(DISTINCT a, b)` KHONG hop le trong Oracle - COUNT(DISTINCT ...)
    chi nhan MOT bieu thuc. Loi nam im trong cong cu vi bang duy nhat tung
    chay qua buoc nay (HT_MA_KVUC) co PK MOT cot. Ca 8 bang lo CN_* co PK
    2-3 cot nen no vo ngay buoc 2.
    """
    import inspect
    src = _ghep(inspect.getsource(wz.Wizard._b_xac_nhan_keycols))
    than = "\n".join(d for d in src.splitlines()
                     if not d.strip().startswith("#"))
    assert "COUNT(DISTINCT {dsc})" not in than, \
        "van dung COUNT(DISTINCT nhieu cot) -> ORA-00909"
    assert "COUNT(DISTINCT" not in than, than
    # Phai dung GROUP BY: dung theo nghia so sanh cua Oracle, mot lan quet
    assert "GROUP BY {dsc}" in than, than


@test
def kiem_duy_nhat_khong_duoc_noi_cot_bang_hai_gach():
    """Noi cot bang `||` thi 'A'||'BC' = 'AB'||'C' -> bao trung khoa OAN."""
    import inspect
    src = _ghep(inspect.getsource(wz.Wizard._b_xac_nhan_keycols))
    than = "\n".join(d for d in src.splitlines()
                     if not d.strip().startswith("#"))
    # Khong duoc sinh bieu thuc noi cot trong phan kiem duy nhat
    assert "'||'" not in than and '"||"' not in than, than


# ===== 10. Dinh dang so theo chuan Viet (design-system.md muc 8.4)
@test
def so_dinh_dang_theo_chuan_Viet():
    """Thap phan DAU PHAY, hang nghin DAU CHAM.

    Cai bay: doi truc tiep `.replace(",", ".")` roi `.replace(".", ",")` se
    doi LAI tat ca ve dau phay - phai qua mot ky tu trung gian. Test nay giu
    dung cho do.
    """
    import io
    import re
    src = io.open(ROOT / "app" / "main.py", encoding="utf-8").read()
    m = re.search(r"def so_vn.*?\n\ndef fmt_bytes", src, re.S)
    assert m, "khong thay so_vn()"
    ns = {}
    exec(m.group(0).replace("\n\ndef fmt_bytes", ""), ns)
    so_vn = ns["so_vn"]
    assert so_vn("305.4") == "305,4"
    assert so_vn("32,768") == "32.768"
    assert so_vn("1,294,309") == "1.294.309"
    # Ca hai dau cung luc - day la ca de vo nhat
    assert so_vn("2,480.5") == "2.480,5"
    assert so_vn("-") == "-"


# ===== 11. Bo quyen bat buoc phai KHOP voi script that
@test
def bo_quyen_bang_dich_phai_khop_script_nap():
    """Chot kiem quyen phai doi DU nhung gi script nap THAT lam.

    Lan dau viet chot nay toi chi kiem `GGADM_LOAD.INSERT` va
    `GGADM_RO.SELECT`, bo sot `GGADM_LOAD.SELECT`. Ket qua: buoc 15 cua quy
    trinh #4 vo voi ORA-01031 NGAY TAI LOP CHAN 1 (`SELECT 1 FROM <dich>`
    de kiem bang rong) - sau khi DA dung Replicat. Tuc la chot dung de chan
    cai nay lai cho no di qua.

    Test nay neo bo quyen vao HAI dau: danh sach khai bao, va cai script
    nap thuc su can.
    """
    assert "GGADM_LOAD.INSERT" in wz.QUYEN_BANG_DICH
    assert "GGADM_LOAD.SELECT" in wz.QUYEN_BANG_DICH, \
        "thieu GGADM_LOAD.SELECT -> lop chan 1 cua script nap se ORA-01031"
    assert "GGADM_RO.SELECT" in wz.QUYEN_BANG_DICH

    # Script nap DOC bang dich (lop chan 1) -> can SELECT
    w = WGia([b for b, _ in LO], dict(LO))
    sql = w._sql_nap_lo(_run_lo(), [b for b, _ in LO])
    assert 'SELECT 1 FROM ODS."CN_CH"' in sql, \
        "script nap khong con doc bang dich - xem lai bo quyen can thiet"
    # ... va GHI vao bang dich -> can INSERT
    assert 'INSERT /*+ APPEND */ INTO ODS."CN_CH"' in sql

    # Chot o buoc KIEM_BANG_DICH phai dung DUNG danh sach do, khong viet
    # cung lai mot ban sao (hai ban sao thi som muon se lech nhau).
    import inspect
    src = inspect.getsource(wz.Wizard._b_kiem_bang_dich)
    assert "QUYEN_BANG_DICH" in src, \
        "buoc kiem bang dich khong dung QUYEN_BANG_DICH -> de lech"
