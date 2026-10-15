"""Test khung an toan L1/L2.

Phu het cac nhanh TU CHOI. Cac nhanh nay la ly do module ton tai, nen chung
phai co test - mot chot an toan khong co test thi khong biet no con hoat dong
hay da bi mot lan refactor lam vo im lang.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.safety import L0, L1, L2, Denied, Guard                 # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# -------------------------------------------------------------------- gia lap
@dataclass
class FakeSnap:
    ts: int = field(default_factory=lambda: int(time.time()))
    read_only: bool = False
    fs: dict = field(default_factory=lambda: {"avail_gb": 315.0})
    age_s: int = 3


class FakeCollector:
    def __init__(self, **kw):
        self.snap = FakeSnap(**kw)


class FakeCfg:
    poll_seconds = 30


class FakeAuth:
    """Ghi lai cac dong audit de test kiem duoc la CO ghi audit."""

    def __init__(self):
        self.rows: list[dict] = []

    @staticmethod
    def has_role(user, need):
        rank = {"VIEWER": 0, "OPERATOR": 1, "ADMIN": 2}
        return rank.get(user["role"], -1) >= rank[need]

    def audit(self, username, peer, level, action, **kw):
        self.rows.append(dict(username=username, level=level, action=action, **kw))


ADMIN = {"username": "admin", "role": "ADMIN"}
OPER = {"username": "dba1", "role": "OPERATOR"}
VIEW = {"username": "xem", "role": "VIEWER"}


def make(**kw) -> tuple[Guard, FakeAuth]:
    a = FakeAuth()
    return Guard(FakeCfg(), None, FakeCollector(**kw), a), a


# ======================================================================= role
@test
def viewer_khong_lam_duoc_thao_tac_ghi():
    g, a = make()
    for lv in (L1, L2):
        try:
            g.require(VIEW, lv, action="STOP")
        except Denied:
            continue
        raise AssertionError(f"VIEWER phai bi tu choi o muc {lv}")
    assert any(r["level"] == "SECURITY_BLOCK" for r in a.rows), \
        "moi lan tu choi phai keo theo mot dong audit SECURITY_BLOCK"


@test
def operator_lam_duoc_ca_L1_va_L2():
    g, _ = make()
    g.require(OPER, L1, action="STOP")
    g.require(OPER, L2, action="VERIFY_SNAPSHOT")


@test
def chua_dang_nhap_bi_tu_choi():
    g, _ = make()
    try:
        g.require(None, L0)
    except Denied:
        return
    raise AssertionError("None phai bi tu choi")


# ================================================================== read-only
@test
def read_only_chan_moi_thao_tac_ghi_nhung_khong_chan_doc():
    g, a = make(read_only=True, fs={"avail_gb": 18.4})
    g.require(ADMIN, L0)                      # doc van duoc
    for lv in (L1, L2):
        try:
            g.require(ADMIN, lv, action="STOP")
        except Denied as e:
            assert "18.4" in str(e), f"thong bao phai noi ro con bao nhieu: {e}"
            continue
        raise AssertionError(f"read-only phai chan muc {lv}")


@test
def so_lieu_giam_sat_cu_thi_chan():
    """Khong chay thao tac ghi khi khong biet trang thai that cua he thong."""
    g, _ = make()
    g.collector.snap.age_s = 30 * 10          # cu gap 10 lan chu ky poll
    try:
        g.require(ADMIN, L1, action="STOP")
    except Denied as e:
        assert "cũ" in str(e), str(e)
        return
    raise AssertionError("so lieu cu phai chan thao tac ghi")


@test
def chua_co_tick_nao_thi_chan():
    g, _ = make(ts=0)
    try:
        g.require(ADMIN, L1, action="STOP")
    except Denied:
        return
    raise AssertionError("chua co tick thi phai chan")


# ====================================================================== ly do
@test
def ly_do_qua_ngan_bi_tu_choi():
    g, _ = make()
    for xau in ("", "ok", "sua param", "         "):
        try:
            g.check_reason(xau)
        except Denied:
            continue
        raise AssertionError(f"ly do {xau!r} qua ngan ma khong bi tu choi")
    assert g.check_reason("  them bang TBH_PBO vao luong HT  ") \
        == "them bang TBH_PBO vao luong HT"


# ======================================================== pre-flight o muc L2
@test
def L2_preflight_FAIL_thi_khong_nap_duoc():
    """Khong co checkbox 'toi hieu rui ro'. FAIL la het duong tu UI."""
    g, a = make()
    try:
        g.arm(ADMIN, action="VERIFY_SNAPSHOT", target="HT", level=L2,
              reason="verify che do chuan luong HT", preflight_ok=False,
              preflight_note="dia_root")
    except Denied as e:
        # Kiem DAU HIEU ON DINH, khong kiem nguyen van: thong diep
        # nguoi dung doc co the doi (da doi 1 lan khi dich sang tieng
        # Viet co dau) ma luat van dung y nguyen.
        assert "nút" in str(e) or "không đạt" in str(e), str(e)
        assert any(r["level"] == "SECURITY_BLOCK" for r in a.rows)
        return
    raise AssertionError("L2 voi pre-flight FAIL phai bi tu choi ngay o buoc nap")


@test
def L1_preflight_FAIL_van_nap_duoc_nhung_nut_bi_tat_o_UI():
    """L1 hoan tac duoc nen khong chan cung o tang nay.

    Nut bi tat o template (`{% if not pf.ok %}disabled{% endif %}`), va
    `fire()` o muc L1 khong doi pre-flight. Co y: vi du start mot process
    dang STOPPED trong khi trail dang phinh - canh bao nhung khong chan.
    """
    g, _ = make()
    p = g.arm(ADMIN, action="START", target="RHT", level=L1,
              reason="start lai RHT sau khi xu ly xong", preflight_ok=False,
              preflight_note="trail")
    assert p.preflight_ok is False


# ====================================================================== token
@test
def token_dung_mot_lan_duy_nhat():
    g, _ = make()
    p = g.arm(ADMIN, action="STOP", target="RHT", level=L1,
              reason="dung RHT de sua param them bang", preflight_ok=True)
    g.fire(p.token, ADMIN)
    try:
        g.fire(p.token, ADMIN)
    except Denied as e:
        assert "không hợp lệ" in str(e) or "hết hạn" in str(e), str(e)
        return
    raise AssertionError("token phai dung duoc dung MOT lan")


@test
def token_het_han_sau_60_giay():
    g, _ = make()
    p = g.arm(ADMIN, action="STOP", target="RHT", level=L1,
              reason="dung RHT de sua param them bang", preflight_ok=True)
    p.expires = time.monotonic() - 1          # gia lap da qua 60s
    try:
        g.fire(p.token, ADMIN)
    except Denied as e:
        assert "hết hạn" in str(e), str(e)
        return
    raise AssertionError("token qua 60 giay phai het han")


@test
def token_cua_nguoi_khac_khong_dung_duoc():
    g, a = make()
    p = g.arm(ADMIN, action="STOP", target="RHT", level=L1,
              reason="dung RHT de sua param them bang", preflight_ok=True)
    try:
        g.fire(p.token, OPER)
    except Denied as e:
        assert "không thuộc về" in str(e), str(e)
        assert any(r["level"] == "SECURITY_BLOCK" for r in a.rows)
        return
    raise AssertionError("token phai gan voi dung tai khoan da nap")


@test
def token_khong_ton_tai():
    g, _ = make()
    try:
        g.fire("khong-he-co-token-nay", ADMIN)
    except Denied:
        return
    raise AssertionError("token bia phai bi tu choi")


# ================================================================ go tay (L2)
@test
def L2_phai_go_dung_ten():
    g, a = make()
    # Luu y: "ht" KHONG co trong danh sach nay - go tay co y khong phan
    # biet hoa/thuong, xem test ke tiep. Danh sach nay la nhung chuoi KHAC ten.
    for sai in ("", "HT_", "RHT", "H", "HT HT", None):
        p = g.arm(ADMIN, action="VERIFY_SNAPSHOT", target="HT", level=L2,
                  reason="verify che do chuan luong HT", preflight_ok=True)
        try:
            g.fire(p.token, ADMIN, typed=sai, preflight_now=True)
        except Denied as e:
            assert "Gõ sai tên" in str(e) or "không hợp lệ" in str(e), str(e)
            continue
        raise AssertionError(f"go {sai!r} ma khong bi tu choi")
    assert any(r["level"] == "SECURITY_BLOCK" for r in a.rows)


@test
def L2_go_dung_ten_thi_qua_khong_phan_biet_hoa_thuong():
    g, _ = make()
    p = g.arm(ADMIN, action="VERIFY_SNAPSHOT", target="HT", level=L2,
              reason="verify che do chuan luong HT", preflight_ok=True)
    out = g.fire(p.token, ADMIN, typed="  ht  ", preflight_now=True)
    assert out.target == "HT"


@test
def L2_preflight_doi_trang_thai_trong_60_giay_thi_tu_choi():
    """Day la diem khac biet that so voi mot modal xac nhan thong thuong."""
    g, a = make()
    p = g.arm(ADMIN, action="VERIFY_SNAPSHOT", target="HT", level=L2,
              reason="verify che do chuan luong HT", preflight_ok=True)
    try:
        g.fire(p.token, ADMIN, typed="HT", preflight_now=False)
    except Denied as e:
        assert "không còn đạt" in str(e), str(e)
        assert any(r["level"] == "SECURITY_BLOCK" for r in a.rows)
        return
    raise AssertionError("pre-flight doi trang thai phai chan luc ban co")


@test
def read_only_bat_giua_luc_xac_nhan_van_chan_duoc():
    """Dia day trong 60 giay giua nap va ban co - van phai chan."""
    g, _ = make()
    p = g.arm(ADMIN, action="STOP", target="RHT", level=L1,
              reason="dung RHT de sua param them bang", preflight_ok=True)
    g.collector.snap.read_only = True
    g.collector.snap.fs = {"avail_gb": 11.0}
    try:
        g.fire(p.token, ADMIN)
    except Denied as e:
        assert "chỉ đọc" in str(e), str(e)
        return
    raise AssertionError("read-only bat sau khi nap van phai chan")


# ======================================================================= audit
@test
def arm_ghi_audit_TRUOC_khi_chay():
    """Du nguoi dung bo giua duong thi van con dau vet la da co y dinh lam gi."""
    g, a = make()
    g.arm(ADMIN, action="STOP", target="RHT", level=L1,
          reason="dung RHT de sua param them bang", preflight_ok=True)
    arm_rows = [r for r in a.rows if r["action"] == "ARM_STOP"]
    assert len(arm_rows) == 1, a.rows
    assert arm_rows[0]["level"] == "L1"
    assert arm_rows[0]["target"] == "RHT"
    assert "dung RHT" in arm_rows[0]["reason"]


@test
def pending_tu_don_khi_het_han():
    g, _ = make()
    p = g.arm(ADMIN, action="STOP", target="RHT", level=L1,
              reason="dung RHT de sua param them bang", preflight_ok=True)
    assert g.pending_of(p.token) is not None
    g._pending[p.token].expires = time.monotonic() - 1
    assert g.pending_of(p.token) is None, "pending het han phai bi don"


# ============================== LUONG_MOI: go tay TEN EXTRACT (2026-10-15)
@test
def luong_moi_go_tay_TEN_EXTRACT_chu_khong_phai_ten_bang():
    """Doi tuong nguy hiem la tien trinh doc redo sap dang ky vao DB nguon.

    Go ten bang la go nham thu khong nguy hiem - phai bi tu choi.
    """
    g, _ = make()
    p = g.arm(ADMIN, action="AUTO_NEW_STREAM", target="HT_MA_KENH_BAN",
              level=L2, reason="tao luong moi cho bang HT_MA_KENH_BAN",
              preflight_ok=True, extra={"go_tay": "ETBC2"})
    try:
        g.fire(p.token, ADMIN, typed="HT_MA_KENH_BAN", preflight_now=True)
    except Denied as e:
        assert "ETBC2" in str(e), str(e)
    else:
        raise AssertionError("go ten bang ma van ban co duoc")

    p2 = g.arm(ADMIN, action="AUTO_NEW_STREAM", target="HT_MA_KENH_BAN",
               level=L2, reason="tao luong moi cho bang HT_MA_KENH_BAN",
               preflight_ok=True, extra={"go_tay": "ETBC2"})
    # khong phan biet hoa thuong - go dung ten la duoc
    assert g.fire(p2.token, ADMIN, typed="etbc2", preflight_now=True)
