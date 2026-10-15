"""Test THAO TAC NHANH - chu yeu la cac nhanh TU CHOI.

Mot bo lai tu dong chay het quy trinh thi gia tri cua no nam o cho no BIET
DUNG, khong phai o cho no chay duoc. Cac ca duoi day khoa lai dung nhung
truong hop phai tu choi, va khoa lai rang bo lai KHONG bo mot lop kiem nao.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auto                                          # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _con(**kw):
    """DB trong bo nho voi mot dong managed_table."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE managed_table (src_owner TEXT, src_table TEXT,"
        " tgt_owner TEXT, tgt_table TEXT, stream TEXT, prepared INT,"
        " in_extract INT, has_map INT)")
    d = dict(src_owner="KT_BSH", src_table="T1", tgt_owner="ODS",
             tgt_table="T1", stream="HT", prepared=0, in_extract=0, has_map=0)
    d.update(kw)
    if d.get("_khong_co_dong"):
        return c
    c.execute(
        "INSERT INTO managed_table VALUES (?,?,?,?,?,?,?,?)",
        (d["src_owner"], d["src_table"], d["tgt_owner"], d["tgt_table"],
         d["stream"], d["prepared"], d["in_extract"], d["has_map"]))
    return c


def _loi(fn):
    try:
        fn()
    except auto.AutoError as e:
        return str(e)
    raise AssertionError("dang le phai tu choi ma khong tu choi")


# ============================================ cac nhanh TU CHOI
@test
def tu_choi_bang_ngoai_danh_sach_quan_ly():
    """Cong cu khong duoc cham vao bang ngoai pham vi - chot lop 5."""
    c = _con(_khong_co_dong=True)
    m = _loi(lambda: auto.kiem_dieu_kien(c, "ADD_TABLE", "T1"))
    assert "không nằm trong danh sách quản lý" in m, m


@test
def tu_choi_khi_nguon_va_dich_KHAC_TEN():
    """Yeu cau cua nguoi dung: che do nhanh chi ap cho bang cung ten."""
    c = _con(tgt_table="T1_ODS")
    m = _loi(lambda: auto.kiem_dieu_kien(c, "ADD_TABLE", "T1"))
    assert "KHÁC TÊN" in m, m
    # Ca dong bo lai cung phai tu choi, khong chi them bang
    c2 = _con(tgt_table="T1_ODS", has_map=1)
    m2 = _loi(lambda: auto.kiem_dieu_kien(c2, "RESYNC_TABLE", "T1"))
    assert "KHÁC TÊN" in m2, m2


@test
def tu_choi_them_bang_khi_bang_DA_du_TABLE_va_MAP():
    c = _con(in_extract=1, has_map=1)
    m = _loi(lambda: auto.kiem_dieu_kien(c, "ADD_TABLE", "T1"))
    assert "Đồng bộ lại bảng" in m, m


@test
def tu_choi_dong_bo_lai_khi_bang_CHUA_co_MAP():
    c = _con(has_map=0)
    m = _loi(lambda: auto.kiem_dieu_kien(c, "RESYNC_TABLE", "T1"))
    assert "chưa có dòng MAP" in m, m


@test
def tu_choi_kind_la():
    c = _con()
    m = _loi(lambda: auto.kiem_dieu_kien(c, "NEW_STREAM", "T1"))
    assert "chỉ hỗ trợ" in m, m


@test
def cho_qua_khi_du_dieu_kien():
    r = auto.kiem_dieu_kien(_con(), "ADD_TABLE", "T1")
    assert r["stream"] == "HT" and r["tgt_table"] == "T1"
    r2 = auto.kiem_dieu_kien(_con(has_map=1), "RESYNC_TABLE", "T1")
    assert r2["has_map"] == 1


# ================================ bo lai KHONG duoc bo lop kiem nao
@test
def bo_lai_van_do_pre_flight_truoc_TUNG_buoc():
    """Day la ly do che do nhanh con an toan. Mat dong nay la mat tat ca."""
    src = inspect.getsource(auto.TuDong._chay)
    assert "self.pre.run(" in src, "bo lai khong do pre-flight"
    assert "if not pf.ok" in src, "do pre-flight ma khong CHAN khi khong dat"
    # Chan la DUNG hang, khong phai ghi log roi di tiep
    i = src.index("if not pf.ok")
    assert "return" in src[i:i + 400], "khong dat ma van di tiep"


@test
def bo_lai_van_qua_dieu_kien_vao_cua_engine():
    src = inspect.getsource(auto.TuDong._chay)
    assert "dieu_kien_vao(" in src
    i = src.index("if not ok_vao")
    assert "return" in src[i:i + 300]


@test
def bo_lai_chay_buoc_qua_DUNG_engine_khong_tu_lam():
    """Phai goi `wizard.chay_buoc`, khong duoc tu goi `_b_<buoc>`.

    Goi tat vao handler se bo qua: ghi trang thai buoc, day cur_step, va
    luat "khong buoc nao chay khi buoc truoc chua PASS".
    """
    src = inspect.getsource(auto.TuDong._chay)
    assert "self.wizard.chay_buoc(" in src
    assert "_b_" not in src, "bo lai goi tat vao handler cua buoc"


@test
def bo_lai_van_kiem_chung_tren_DB_sau_viec_nen():
    """`kiem_job` la cho chay `_xn_<buoc>`. Bo no la tin vao ma thoat 0."""
    src = inspect.getsource(auto.TuDong._cho_job)
    assert "kiem_job(" in src
    assert '"FAIL"' in src and "return False" in src


@test
def bo_lai_co_tran_thoi_gian():
    """Khong de mot luot treo mai neu viec nen chet khong ai cap nhat."""
    assert auto.CHO_JOB_TOI_DA > 0 and auto.TONG_TOI_DA > auto.CHO_JOB_TOI_DA
    src = inspect.getsource(auto.TuDong._chay)
    assert "TONG_TOI_DA" in src
    assert "CHO_JOB_TOI_DA" in inspect.getsource(auto.TuDong._cho_job)


@test
def dung_giua_duong_thi_KHONG_hoan_tac():
    """Cac buoc da PASS van co hieu luc that - noi ro, khong am tham."""
    src = inspect.getsource(auto.TuDong._ghi_loi)
    assert "KHONG hoan tac" in src or "KHONG dong phien" in src
    # Ghi ly do dung vao phien de UI hien duoc
    assert "note=?" in src


@test
def moi_buoc_mot_dong_vet_thao_tac():
    """Danh doi nghi thuc L2 tung buoc thi it nhat phai ghi vet tung buoc."""
    src = inspect.getsource(auto.TuDong._audit)
    assert "AUTO_STEP" in src
    chay = inspect.getsource(auto.TuDong._chay)
    assert chay.count("self._audit(") >= 2, \
        "phai ghi vet ca khi buoc dat va khi buoc loi"


# ============================== khung theo doi song
@test
def bo_lai_luu_LENH_truoc_khi_chay():
    """Khong luu lenh thi khong con dau vet ve "no da chay cai gi".

    Duong bam tay hien ban xem truoc tren man hinh, nguoi dung doc roi moi
    bam. Che do nhanh khong co ai doc - nen phai luu vao
    `wizard_step.preview`, neu khong thi chi con KET QUA chu khong con LENH,
    va luc truy nguoc se khong biet no da lam gi.
    """
    src = inspect.getsource(auto.TuDong._chay)
    assert "xem_truoc(" in src, "khong lay ban xem truoc"
    assert "SET preview=?" in src, "lay ban xem truoc ma khong luu"
    # Phai luu TRUOC khi chay buoc, khong phai sau
    assert src.index("SET preview=?") < src.index("chay_buoc("), \
        "luu lenh SAU khi chay thi mat y nghia theo doi song"


@test
def khung_theo_doi_luon_tu_cap_nhat():
    """Het luot thi dung poll la sai: bam chay xong khung se dung yen."""
    from pathlib import Path
    t = (ROOT / "web" / "templates" / "_nhanh_song.html").read_text(
        encoding="utf-8")
    assert 'hx-get="/nhanh/song"' in t
    # Thuoc tinh hx-trigger phai o NGOAI dieu kien, chi NHIP la thay doi
    i = t.index('hx-get="/nhanh/song"')
    khoi = t[i:i + 400]
    assert "hx-trigger=" in khoi
    assert "'3s' if co_chay else '10s'" in khoi, \
        "nhip poll khong doi theo trang thai"


@test
def khung_theo_doi_chi_doc():
    """Mot khung monitoring khong duoc chay bat cu thao tac ghi nao."""
    from pathlib import Path
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    i = src.index('async def nhanh_song')
    than = src[i:src.index("\n@app.", i)]
    for xau in ("chay_buoc", "kiem_job", "control.", "tudong.chay",
                "INSERT", "UPDATE", "DELETE"):
        assert xau not in than, "khung theo doi goi %r - phai CHI DOC" % xau


# ================================ KEYCOLS dat truoc + chay tiep (2026-10-15)
def _con_phien(**kw):
    """DB trong bo nho voi mot dong wizard_run."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE wizard_run (id INTEGER PRIMARY KEY, kind TEXT,"
              " state TEXT, stream TEXT, src_table TEXT, cur_step INT,"
              " keycols TEXT, note TEXT, updated_at INT)")
    d = dict(kind="ADD_TABLE", state="OPEN", stream="HT",
             src_table="HT_MA_KENH_BAN", cur_step=2, keycols=None, note=None)
    d.update(kw)
    c.execute("INSERT INTO wizard_run (id,kind,state,stream,src_table,"
              "cur_step,keycols,note,updated_at) VALUES (7,?,?,?,?,?,?,?,0)",
              (d["kind"], d["state"], d["stream"], d["src_table"],
               d["cur_step"], d["keycols"], d["note"]))
    return c


class _WizardGia:
    def __init__(self, con):
        self.con = con

    def lay(self, rid):
        r = self.con.execute(
            "SELECT * FROM wizard_run WHERE id=?", (rid,)).fetchone()
        if r is None:
            raise RuntimeError(f"không có quy trình #{rid}")
        return dict(r)


def _tudong(con):
    return auto.TuDong(cfg=None, con=con, wizard=_WizardGia(con),
                       pre=None, auth=None, pf_cua=None)


@test
def dat_keycols_chuan_hoa_HOA_va_dau_phay():
    c = _con_phien()
    _tudong(c).dat_keycols(7, " ma_dvi ,ma_kenh,, ")
    assert c.execute("SELECT keycols FROM wizard_run WHERE id=7"
                     ).fetchone()[0] == "MA_DVI, MA_KENH"


@test
def chay_tiep_tu_choi_khi_phien_da_dong():
    c = _con_phien(state="DONE")
    m = _loi(lambda: _tudong(c).kiem_tiep(7))
    assert "không chạy tiếp được" in m, m


@test
def chay_tiep_tu_choi_khi_phien_DANG_chay():
    """Hai bo lai cung di tren mot phien la chong buoc."""
    t = _tudong(_con_phien())
    t.dang_chay.add(7)
    m = _loi(lambda: t.kiem_tiep(7))
    assert "đang chạy tự động" in m, m


@test
def chay_tiep_tu_choi_khi_khong_co_phien():
    m = _loi(lambda: _tudong(_con_phien()).kiem_tiep(999))
    assert "999" in m, m


@test
def chay_tiep_cho_qua_khi_phien_dang_mo_va_khong_ai_chay():
    run = _tudong(_con_phien()).kiem_tiep(7)
    assert run["cur_step"] == 2 and run["src_table"] == "HT_MA_KENH_BAN"


@test
def o_go_tay_L2_KHONG_con_chan_dan():
    """Yeu cau nguoi dung 2026-10-15: cho copy/paste binh thuong."""
    tpl = (ROOT / "web" / "templates" / "_confirm.html").read_text(encoding="utf-8")
    assert "onpaste" not in tpl and "ondrop" not in tpl
    # nhung ten VAN phai khop - chot that nam o may chu
    assert 'name="typed"' in tpl


# ================================= LUONG_MOI qua che do nhanh (2026-10-15)
def _con_lm(**kw):
    """DB co CA managed_table CA wizard_run - nhanh LUONG_MOI doc ca hai."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE managed_table (src_owner TEXT, src_table TEXT,"
        " tgt_owner TEXT, tgt_table TEXT, stream TEXT, prepared INT,"
        " in_extract INT, has_map INT, handlecollisions INT, updated_at INT)")
    c.execute("CREATE TABLE wizard_run (id INTEGER PRIMARY KEY, kind TEXT,"
              " state TEXT, stream TEXT, src_table TEXT, cur_step INT,"
              " keycols TEXT, note TEXT, ctx_json TEXT, updated_at INT,"
              " created_extract TEXT, created_replicat TEXT,"
              " created_trail_prefix TEXT, created_ckpt_table TEXT)")
    d = dict(src_owner="KT_BSH", src_table="HT_MA_KENH_BAN", tgt_owner="ODS",
             tgt_table="HT_MA_KENH_BAN", stream=None, prepared=0,
             in_extract=0, has_map=0)
    d.update(kw)
    c.execute("INSERT INTO managed_table(src_owner,src_table,tgt_owner,"
              "tgt_table,stream,prepared,in_extract,has_map) VALUES"
              " (?,?,?,?,?,?,?,?)",
              (d["src_owner"], d["src_table"], d["tgt_owner"], d["tgt_table"],
               d["stream"], d["prepared"], d["in_extract"], d["has_map"]))
    return c


@test
def luong_moi_cho_qua_khi_bang_chua_thuoc_luong_nao():
    r = auto.kiem_dieu_kien(_con_lm(), "LUONG_MOI", "HT_MA_KENH_BAN")
    assert r["src_table"] == "HT_MA_KENH_BAN"


@test
def luong_moi_tu_choi_khi_bang_DA_co_MAP():
    m = _loi(lambda: auto.kiem_dieu_kien(
        _con_lm(has_map=1), "LUONG_MOI", "HT_MA_KENH_BAN"))
    assert "đã có dòng MAP" in m, m


@test
def luong_moi_tu_choi_khi_bang_da_co_dong_TABLE():
    m = _loi(lambda: auto.kiem_dieu_kien(
        _con_lm(in_extract=1), "LUONG_MOI", "HT_MA_KENH_BAN"))
    assert "dòng TABLE" in m, m


@test
def luong_moi_tu_choi_khi_bang_da_gan_luong():
    m = _loi(lambda: auto.kiem_dieu_kien(
        _con_lm(stream="HT"), "LUONG_MOI", "HT_MA_KENH_BAN"))
    assert "thuộc luồng HT" in m, m


@test
def luong_moi_tu_choi_khi_dang_co_phien_tao_luong_khac():
    c = _con_lm()
    c.execute("INSERT INTO wizard_run(id,kind,state,src_table) VALUES"
              " (3,'NEW_STREAM','OPEN','CN_CH')")
    m = _loi(lambda: auto.kiem_dieu_kien(c, "LUONG_MOI", "HT_MA_KENH_BAN"))
    assert "Tối đa MỘT phiên" in m, m


@test
def luong_moi_van_tu_choi_khi_nguon_dich_KHAC_TEN():
    m = _loi(lambda: auto.kiem_dieu_kien(
        _con_lm(tgt_table="HT_MA_KENH_BAN_ODS"), "LUONG_MOI", "HT_MA_KENH_BAN"))
    assert "KHÁC TÊN" in m, m


@test
def NEW_STREAM_van_KHONG_phai_che_do_nhanh():
    """Ban thu cong 13 buoc co y nam ngoai; ban tron bo la LUONG_MOI."""
    m = _loi(lambda: auto.kiem_dieu_kien(_con_lm(), "NEW_STREAM", "HT_MA_KENH_BAN"))
    assert "chỉ hỗ trợ" in m, m


# --------------------------------------------------------- dat_ten_moi
def _tudong_lm(con):
    return auto.TuDong(cfg=None, con=con, wizard=None, pre=None, auth=None,
                       pf_cua=None)


@test
def dat_ten_moi_luu_du_bon_ten_va_ma_luong():
    c = _con_lm()
    c.execute("INSERT INTO wizard_run(id,kind,state,src_table,cur_step)"
              " VALUES (9,'LUONG_MOI','OPEN','HT_MA_KENH_BAN',4)")
    _tudong_lm(c).dat_ten_moi(9, {"extract": "etbc2", "replicat": "rbc2",
                               "trail_prefix": "bc", "stream_id": "bc2"})
    r = c.execute("SELECT * FROM wizard_run WHERE id=9").fetchone()
    assert r["created_extract"] == "ETBC2"
    assert r["created_replicat"] == "RBC2"
    assert r["created_trail_prefix"] == "bc"
    assert r["created_ckpt_table"] == "OGG_BSH.RBC2"
    assert '"sid": "BC2"' in r["ctx_json"]


@test
def dat_ten_moi_TU_CHOI_ten_xau_ngay_tu_dau():
    from app.adapters import gg

    c = _con_lm()
    c.execute("INSERT INTO wizard_run(id,kind,state,src_table,cur_step)"
              " VALUES (9,'LUONG_MOI','OPEN','HT_MA_KENH_BAN',4)")
    t = _tudong_lm(c)
    xau = [{"extract": "RHT", "replicat": "RBC2", "trail_prefix": "bc"},
           {"extract": "ETBC2", "replicat": "ETX", "trail_prefix": "bc"},
           {"extract": "ETBC2", "replicat": "RBC2", "trail_prefix": "b"},
           {"extract": "ETBC2", "replicat": "RBC2", "trail_prefix": "pm"}]
    for ten in xau:
        try:
            t.dat_ten_moi(9, ten)
        except (gg.Blocked, auto.AutoError):
            continue
        raise AssertionError("ten xau ma van dat duoc: %s" % ten)


# ------------------------------------------------------- tu don khi hong
class _WizardDon:
    """Wizard gia: nho lai co bi goi don sach khong."""

    def __init__(self, con, hong=False):
        self.con = con
        self.hong = hong
        self.da_don = []

    def lay(self, rid):
        return dict(self.con.execute(
            "SELECT * FROM wizard_run WHERE id=?", (rid,)).fetchone())

    async def rollback_new_stream(self, rid, who, reason):
        self.da_don.append((rid, who, reason))
        if self.hong:
            raise RuntimeError("unregister extract THAT BAI")
        return {"msg": "da don sach"}


class _AuthGia:
    def __init__(self):
        self.dong = []

    def audit(self, *a, **kw):
        self.dong.append((a, kw))


def _phien(c, **kw):
    d = dict(id=9, kind="LUONG_MOI", state="OPEN", src_table="HT_MA_KENH_BAN",
             cur_step=11, note="[thao tác nhanh dừng] bước 11 lỗi",
             created_extract="ETBC2", created_replicat="RBC2",
             created_trail_prefix="bc", created_ckpt_table="OGG_BSH.RBC2")
    d.update(kw)
    c.execute("INSERT INTO wizard_run(id,kind,state,src_table,cur_step,note,"
              "created_extract,created_replicat,created_trail_prefix,"
              "created_ckpt_table) VALUES (:id,:kind,:state,:src_table,"
              ":cur_step,:note,:created_extract,:created_replicat,"
              ":created_trail_prefix,:created_ckpt_table)", d)


def _chay_don(c, hong=False):
    t = _tudong_lm(c)
    t.wizard = _WizardDon(c, hong=hong)
    t.auth = _AuthGia()
    asyncio.run(t._don_neu_hong(9, "admin"))
    return t


@test
def hong_giua_chung_thi_TU_DON_ngay():
    c = _con_lm()
    _phien(c)
    t = _chay_don(c)
    assert t.wizard.da_don, "khong tu don"
    assert t.auth.dong and t.auth.dong[0][0][3] == "AUTO_ROLLBACK"
    assert c.execute("SELECT note FROM wizard_run WHERE id=9"
                     ).fetchone()[0].startswith("[tự dọn]")


@test
def KHONG_tu_don_khi_phien_chay_xong_binh_thuong():
    c = _con_lm()
    _phien(c, state="DONE", note=None)
    assert not _chay_don(c).wizard.da_don


@test
def KHONG_tu_don_khi_chua_chot_ten_vi_chua_tao_gi():
    c = _con_lm()
    _phien(c, created_extract=None, cur_step=3)
    assert not _chay_don(c).wizard.da_don


@test
def KHONG_tu_don_khi_quy_trinh_la_ADD_TABLE():
    c = _con_lm()
    _phien(c, kind="ADD_TABLE")
    assert not _chay_don(c).wizard.da_don


@test
def don_hong_thi_bao_do_va_KHONG_don_lan_hai():
    c = _con_lm()
    _phien(c)
    t = _chay_don(c, hong=True)
    assert len(t.wizard.da_don) == 1
    note = c.execute("SELECT note FROM wizard_run WHERE id=9").fetchone()[0]
    assert note.startswith("[tự dọn KHÔNG XONG]"), note
    # lan sau khong duoc don nua
    assert not _chay_don(c).wizard.da_don
