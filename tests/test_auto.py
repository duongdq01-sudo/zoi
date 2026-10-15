"""Test THAO TAC NHANH - chu yeu la cac nhanh TU CHOI.

Mot bo lai tu dong chay het quy trinh thi gia tri cua no nam o cho no BIET
DUNG, khong phai o cho no chay duoc. Cac ca duoi day khoa lai dung nhung
truong hop phai tu choi, va khoa lai rang bo lai KHONG bo mot lop kiem nao.
"""

from __future__ import annotations

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
