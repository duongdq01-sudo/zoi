"""Test sinh DDL bang dich.

Khoa lai cac bay da gap that khi sinh DDL tu metadata nguon. Khong can
Oracle: `sinh_ddl` chi nhan dict metadata.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ddl import DdlError, kieu_cot, sinh_ddl                # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _c(no, ten, **kw):
    d = dict(column_id=no, column_name=ten, data_type="VARCHAR2",
             data_length=10, char_length=10, char_used="B",
             data_precision=None, data_scale=None, nullable="Y")
    d.update(kw)
    return d


# ------------------------------------------------- kieu chuoi va don vi
@test
def nvarchar2_KHONG_duoc_them_qualifier_CHAR():
    """`NVARCHAR2(200 CHAR)` la ORA-00907. Da gap that 2026-10-15."""
    assert kieu_cot(_c(1, "TEN", data_type="NVARCHAR2", char_length=200,
                       data_length=400, char_used="C")) == "NVARCHAR2(200)"


@test
def nchar_cung_KHONG_duoc_them_qualifier_CHAR():
    assert kieu_cot(_c(1, "MA", data_type="NCHAR", char_length=3,
                       data_length=6, char_used="C")) == "NCHAR(3)"


@test
def varchar2_theo_ky_tu_thi_PHAI_giu_CHAR():
    """Mang sai don vi thi cot ngan hon nguon -> ORA-12899 luc apply."""
    assert kieu_cot(_c(1, "TEN", char_length=50, data_length=200,
                       char_used="C")) == "VARCHAR2(50 CHAR)"


@test
def varchar2_theo_byte_thi_khong_co_qualifier():
    assert kieu_cot(_c(1, "MA", char_length=10, data_length=10,
                       char_used="B")) == "VARCHAR2(10)"


# ------------------------------------------------------------- kieu so
@test
def number_khong_co_do_chinh_xac_thi_de_tran():
    assert kieu_cot(_c(1, "N", data_type="NUMBER")) == "NUMBER"


@test
def number_co_precision_va_scale():
    assert kieu_cot(_c(1, "N", data_type="NUMBER", data_precision=12,
                       data_scale=2)) == "NUMBER(12,2)"


# ------------------------------------------------------- nhanh TU CHOI
@test
def kieu_can_phan_xet_thi_TU_CHOI_chu_khong_doan():
    for t in ("CLOB", "BLOB", "XMLTYPE", "LONG"):
        try:
            kieu_cot(_c(1, "X", data_type=t))
        except DdlError:
            continue
        raise AssertionError(f"{t}: đáng lẽ phải từ chối")


# --------------------------------------------------- toan bo script DDL
@test
def script_khong_bao_gio_co_N_kieu_kem_CHAR():
    sql = sinh_ddl(
        tgt_owner="ODS", tgt_table="HT_MA_KENH_BAN",
        cols=[_c(1, "MA_KENH", char_used="B", nullable="N"),
              _c(2, "TEN", data_type="NVARCHAR2", char_length=200,
                 data_length=400, char_used="C")],
        pk=[{"column_name": "MA_KENH", "position": 1}], idx=[],
        src="KT_BSH.HT_MA_KENH_BAN")
    assert "NVARCHAR2(200)" in sql
    assert "NVARCHAR2(200 CHAR)" not in sql
    # va cau tao bang van dong ngoac dung
    than = sql.split("CREATE TABLE", 1)[1]
    assert than.count("(") == than.count(")")
