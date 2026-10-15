"""Chay toan bo test. Khong can pytest.

    python tests/run_all.py

Khong dung pytest co y: venv tren server chi co dung nhung goi can de chay
app. Mot bo test ma phai cai them goi moi chay duoc thi se khong ai chay.
Khong can Oracle, khong can ggsci, khong can SQLite - toan bo la logic thuan.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# In duoc tieng Viet ca khi console KHONG phai UTF-8.
#
# May dev la Windows, console mac dinh cp1252: mot thong diep co dau lam
# `print` nem UnicodeEncodeError va bo test SAP NGAY giua duong - khong phai
# test hong, ma la trinh chay test hong. `deploy.sh` goi file nay nhu mot cong
# chan truoc khi day code, nen loi nay chan ca duong deploy.
# `errors="replace"` thay vi "strict": mat dau tieng Viet luc IN con hon la
# mat ca ket qua test.
for _luong in (sys.stdout, sys.stderr):
    try:
        _luong.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):            # luong bi chuyen huong
        pass

MODULES = ["test_gg_parse", "test_safety", "test_params", "test_jobs",
           "test_lo_nhieu_bang", "test_auto", "test_cho_khoiphuc",
           "test_notify", "test_ddl", "test_toml_ghi", "test_luong_moi"]


def main() -> int:
    tong = dat = 0
    hong: list[tuple[str, str, str]] = []

    for ten in MODULES:
        m = importlib.import_module(ten)
        print(f"\n### {ten}  ({len(m.TESTS)} ca)")
        for fn in m.TESTS:
            tong += 1
            try:
                fn()
            except Exception:                                   # noqa: BLE001
                hong.append((ten, fn.__name__, traceback.format_exc()))
                print(f"  HONG  {fn.__name__}")
            else:
                dat += 1
                print(f"  dat   {fn.__name__}")

    print("\n" + "=" * 68)
    if hong:
        for ten, fn, tb in hong:
            print(f"\n--- {ten}.{fn} ---\n{tb}")
        print(f"KET QUA: {dat}/{tong} dat, {len(hong)} HONG")
        return 1
    print(f"KET QUA: {dat}/{tong} dat - SACH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
