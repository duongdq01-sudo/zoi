"""Test lenh GOI Y commit o hai buoc cho moc (CHO_LOW_WM, CHO_AP_HET).

Phien #12 (2026-10-22, ADD_TABLE TBH_PBO_CH vao PMNV2): buoc 20 dung gan 3
phut vi ETPMNV2 dung o SCN 449.548.191 < moc nap 449.576.349 - luong vang,
ca Extract cung khong nhich nen duong ap rong cung khong dung duoc. Qua duoc
chi nho mot giao dich that o nguon. Nguoi dung chon: buoc cho CHI IN lenh
goi y, KHONG tu ghi vao nguon (nguon chi doc voi cong cu).

So lieu trong file nay la so lieu THAT doc tu server luc do.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import wizard as wz                                  # noqa: E402
import test_ap_rong as ar                                     # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


MX = 449576349             # moc nap phien #12
SCN_ET = "449548191"       # ETPMNV2 dung yen o day suot 09:39-09:44
LOW = "449545247"          # Low WM RPMNV2 suot 2 phut 46 giay
HIGH = "449547703"

# Cot that cua KT_BSH.TBH_PBO_CH (rut gon): 3 cot khoa dung dau
COT = [{"column_name": "MA_DVI", "data_type": "VARCHAR2"},
       {"column_name": "SO_ID_PS", "data_type": "NUMBER"},
       {"column_name": "BT", "data_type": "NUMBER"},
       {"column_name": "GHI_CHU", "data_type": "CLOB"},
       {"column_name": "NGAY_HT", "data_type": "DATE"}]


class _Ora(ar._Ora):
    """Them rows/scalar cho catalog nguon. Ghi lai MOI cau SQL de kiem rang
    cong cu KHONG BAO GIO gui lenh ghi."""

    def __init__(self, trigger=None, cot=None, loi_catalog=None):
        super().__init__(dict(ar.RANH))
        self.trigger = trigger or {}
        self.cot = cot or {}
        self.loi_catalog = loi_catalog
        self.sql: list[str] = []

    async def rows(self, sql, **b):
        self.sql.append(sql)
        if self.loi_catalog:
            raise RuntimeError(self.loi_catalog)
        return self.cot.get(b["t"], COT)

    async def scalar(self, sql, **b):
        self.sql.append(sql)
        return self.trigger.get(b["t"], 0)


def _rep_ket(f2):
    return {"low_wm": LOW, "high_wm": HIGH, "read_file": str(f2), "rba": "1404"}


def _phien12(ora, *, bang_luong=()):
    w, con, run, tmp, f2 = ar._phien("ADD_TABLE", ora, maxscn=MX)
    con.execute("UPDATE wizard_run SET keycols='MA_DVI, SO_ID_PS, BT' WHERE id=1")
    no = wz.so_buoc("ADD_TABLE", "CHO_LOW_WM")
    con.execute("INSERT INTO wizard_step(run_id, step_no, name, state)"
                " VALUES (1, ?, 'CHO_LOW_WM', 'DANG')", (no,))
    for b, kc in bang_luong:
        con.execute("INSERT INTO managed_table(src_table, tgt_table, stream, keycols,"
                    " in_extract, has_map) VALUES (?, ?, 'PMNV2', ?, 1, 1)", (b, b, kc))
    return w, con, w.lay(1), f2, no


def _chay(w, run, f2, so_lan=8):
    tra = ar._gia_gg(_rep_ket(f2), {"read_scn": SCN_ET})
    try:
        return ar._chay_cho(w._b_cho_low_wm, run, so_lan=so_lan)
    finally:
        tra()


def _output(con, no):
    return con.execute("SELECT output FROM wizard_step WHERE run_id=1 AND step_no=?",
                       (no,)).fetchone()[0] or ""


@test
def luong_VANG_qua_60s_hien_lenh_UPDATE_cot_KHONG_thuoc_khoa():
    ora = _Ora()
    w, con, run, f2, no = _phien12(ora)
    try:
        _chay(w, run, f2)
    except wz.WizardError as e:
        assert "xem lệnh gợi ý" in str(e), e
    else:
        raise AssertionError("phien #12 khong duoc qua khi Extract dung duoi moc")
    out = _output(con, no)
    assert 'UPDATE KT_BSH."B" SET "NGAY_HT" = "NGAY_HT" WHERE ROWNUM = 1;' in out, out
    assert "COMMIT;" in out and "KHÔNG tự chạy" in out, out


@test
def cong_cu_KHONG_BAO_GIO_gui_lenh_ghi_toi_DB():
    ora = _Ora()
    w, con, run, f2, no = _phien12(ora)
    try:
        _chay(w, run, f2)
    except wz.WizardError:
        pass
    assert ora.sql, "phai co doc catalog de chon cot"
    for s in ora.sql:
        assert s.lstrip().upper().startswith("SELECT"), s


@test
def luong_BAN_qua_truoc_60s_KHONG_hien_goi_y_KHONG_doc_catalog():
    ora = _Ora()
    w, con, run, f2, no = _phien12(ora)
    tra = ar._gia_gg({"low_wm": str(MX + 5), "high_wm": str(MX + 9),
                      "read_file": str(f2), "rba": "1"}, {"read_scn": SCN_ET})
    try:
        r = ar._chay_cho(w._b_cho_low_wm, run, so_lan=1)
    finally:
        tra()
    assert not ora.sql and "GỢI Ý" not in "\n".join(r["output"]), r


@test
def tien_do_ghi_SONG_vao_wizard_step_TRUOC_60s():
    ora = _Ora()
    w, con, run, f2, no = _phien12(ora)
    try:
        _chay(w, run, f2, so_lan=3)       # 0s, 10s, 20s - chua toi nguong
    except wz.WizardError:
        pass
    out = _output(con, no)
    assert "20 giây: " in out and "GỢI Ý" not in out, out


@test
def uu_tien_bang_KHONG_co_trigger_cua_luong():
    ora = _Ora(trigger={"B": 2})
    w, con, run, f2, no = _phien12(ora, bang_luong=[("BH_HD_GOC_DK", "MA_DVI, SO_ID, BT")])
    dong = ar.asyncio.run(w._goi_y_commit(run, set(), 61))
    s = "\n".join(dong)
    assert 'UPDATE KT_BSH."BH_HD_GOC_DK" SET "SO_ID_PS"' in s, s
    assert "CẢNH BÁO" not in s, s


@test
def moi_bang_deu_co_trigger_van_goi_y_nhung_CANH_BAO():
    ora = _Ora(trigger={"B": 2})
    w, con, run, f2, no = _phien12(ora)
    s = "\n".join(ar.asyncio.run(w._goi_y_commit(run, set(), 61)))
    assert 'UPDATE KT_BSH."B"' in s and "CẢNH BÁO: bảng B có 2 trigger" in s, s


@test
def GO_BANG_KHONG_goi_y_chinh_bang_dang_go():
    """Bang dang go da mat dong TABLE: update no khong vao trail."""
    ora = _Ora()
    w, con, run, f2, no = _phien12(ora, bang_luong=[("BH_HD_GOC_DK", "MA_DVI, SO_ID, BT"),
                                                     ("B", "MA_DVI, SO_ID_PS, BT")])
    s = "\n".join(ar.asyncio.run(w._goi_y_commit(run, {"B"}, 61)))
    assert '"BH_HD_GOC_DK"' in s and 'KT_BSH."B"' not in s, s


@test
def loi_catalog_KHONG_lam_hong_buoc_cho_va_tra_mau_lenh():
    ora = _Ora(loi_catalog="ORA-02019: connection description for remote database not found")
    w, con, run, f2, no = _phien12(ora)
    try:
        _chay(w, run, f2)
    except wz.WizardError as e:
        assert "chưa vượt mốc nạp" in str(e), e
    out = _output(con, no)
    assert "<CỘT KHÔNG PHẢI KHOÁ>" in out, out


@test
def KHONG_biet_cot_khoa_thi_KHONG_tu_chon_cot():
    """Khong biet khoa thi khong chung minh duoc cot chon khong phai khoa."""
    ora = _Ora()
    w, con, run, f2, no = _phien12(ora)
    con.execute("UPDATE wizard_run SET keycols=NULL WHERE id=1")
    s = "\n".join(ar.asyncio.run(w._goi_y_commit(w.lay(1), set(), 61)))
    assert "<CỘT KHÔNG PHẢI KHOÁ>" in s, s


@test
def CA_HAI_buoc_cho_moc_deu_co_goi_y():
    import inspect
    for ten in ("_b_cho_low_wm", "_b_cho_ap_het"):
        src = inspect.getsource(getattr(wz.Wizard, ten))
        assert "self._boc_cho(" in src, ten


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
