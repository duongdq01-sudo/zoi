"""Test bieu thuc checksum verify khi bang co cot LOB.

ORA_HASH tren LOB bam CON TRO chu khong bam noi dung - do that 2026-10-23
tren server: cung TO_CLOB('abc xyz') cho 631765356 o dich va 1844087694 qua
DBL_PMNV. De cot LOB trong checksum thi bang LOB nao cung bao lech oan.

Khong can Oracle: `rows` / `resolve_target_table` deu gia.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.adapters.ora import Ora                              # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _expr(cot: list[tuple[str, str]]) -> str:
    o = object.__new__(Ora)

    async def resolve(t):
        return t

    async def rows(sql, **b):
        assert "data_type" in sql, sql
        return [{"column_name": c, "data_type": k} for c, k in cot]
    o.resolve_target_table, o.rows = resolve, rows
    return asyncio.run(o.checksum_expr("BH_KENH_CALLBACK_NK"))


@test
def cot_LOB_bi_bo_khoi_checksum():
    e = _expr([("ID", "NUMBER"), ("DU_LIEU_THO", "CLOB"),
               ("ANH", "BLOB"), ("GHI", "NCLOB"), ("NGAY", "DATE")])
    assert e == """NVL(ORA_HASH("ID"),0)||'|'||NVL(ORA_HASH("NGAY"),0)""", e


@test
def bang_khong_LOB_thi_bieu_thuc_KHONG_doi():
    """hash_expr_sha256 cua cac bang cu phai giu nguyen."""
    e = _expr([("MA_DVI", "VARCHAR2"), ("MA", "VARCHAR2")])
    assert e == """NVL(ORA_HASH("MA_DVI"),0)||'|'||NVL(ORA_HASH("MA"),0)""", e


@test
def bang_toan_LOB_thi_chi_so_so_dong():
    assert _expr([("DU_LIEU", "CLOB")]) == "0"


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
