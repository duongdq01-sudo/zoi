"""Test kenh canh bao - phan tin thu do ADMIN tu go noi dung.

Tu khi `/notify/test` cho ADMIN go noi dung, `Msg.body` khong con la chuoi
do app tu sinh nua. Hai cai phai khoa lai:
  1. notify.log ghi MOI BAN GHI MOT DONG -> body co xuong dong la gia duoc
     mot dong log nhin y nhu that.
  2. tran do dai phai la MOT hang so dung chung, khong chep tay hai noi.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import notify as nt                                  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class _Cfg:
    """Cau hinh toi thieu: Notifier chi can log_dir, notify, creds."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.notify = {"enabled": True}
        self.creds = {}


def _notifier(tmp: Path) -> nt.Notifier:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    return nt.Notifier(_Cfg(tmp), con)


# ----------------------------------------------------------------- mot_dong
@test
def mot_dong_gap_xuong_dong():
    assert nt.mot_dong("a\nb") == "a | b"
    assert nt.mot_dong("a\r\nb") == "a | b"


@test
def mot_dong_bo_dong_rong_va_khoang_trang_thua():
    assert nt.mot_dong("a\n\n   \n  b  ") == "a | b"


@test
def mot_dong_chuoi_thuong_giu_nguyen():
    assert nt.mot_dong("khong co gi") == "khong co gi"


# ------------------------------------------------------- log luon MOT dong
@test
def tin_nhieu_dong_van_ghi_log_MOT_dong():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        n = _notifier(tmp)
        n.send_test("dba1", "dong 1\n2026-01-01 00:00:00 TEST INFO - - :: gia mao")
        dong = (tmp / "notify.log").read_text(encoding="utf-8").splitlines()
        assert len(dong) == 1, dong
        assert "gia mao" in dong[0]
        assert " | " in dong[0]


@test
def tin_thu_mac_dinh_van_ghi_duoc_log():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        res = _notifier(tmp).send_test("dba1")
        assert res == [("log", True, "ok")], res
        assert (tmp / "notify.log").exists()


# ------------------------------------------------------------- noi dung tin
@test
def noi_dung_tu_go_duoc_dua_vao_than_tin():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _notifier(tmp).send_test("dba1", "kiem tra group van hanh")
        txt = (tmp / "notify.log").read_text(encoding="utf-8")
        assert "kiem tra group van hanh" in txt
        assert "kênh đã thông" not in txt        # cau mac dinh phai bien mat


@test
def de_trong_thi_dung_cau_mac_dinh():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _notifier(tmp).send_test("dba1", "   ")
        assert "kênh đã thông" in (tmp / "notify.log").read_text(encoding="utf-8")


# ------------------------------------------------------------- tran do dai
@test
def dung_bang_tran_thi_van_gui():
    with tempfile.TemporaryDirectory() as d:
        n = _notifier(Path(d))
        n.send_test("dba1", "x" * nt.MAX_TEST_MSG)


@test
def vuot_tran_thi_nem_ValueError():
    with tempfile.TemporaryDirectory() as d:
        n = _notifier(Path(d))
        try:
            n.send_test("dba1", "x" * (nt.MAX_TEST_MSG + 1))
        except ValueError:
            return
        raise AssertionError("vuot tran ma van gui")


@test
def tran_la_MOT_hang_so_dung_chung():
    """main.py phai lay nguong tu notify, khong chep tay so 1000."""
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "MAX_TEST_MSG" in src
    assert "> 1000" not in src
