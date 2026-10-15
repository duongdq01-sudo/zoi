#!/usr/bin/env python3
"""Kiem cu phap Jinja cua MOI template. Chay tren server (co jinja2).

    venv/bin/python3 tools/lint_tpl.py

VI SAO CAN CONG NAY
Mot template vo cu phap chi lo ra khi NGUOI DUNG mo dung trang do - app van
start binh thuong, `/healthz` van 200, compile Python van sach. Da xay ra
that: mot ban dich chen thua `}}` lam vo bieu thuc dieu kien trong
`_dashboard_body.html`; chuoi `if ck.last_update_ts else '-' }}` hien ra nhu
van ban tren trang Tong quan, va neu `last_update_ts` la None thi ca trang
tra 500. Compile Python khong bat duoc, test khong bat duoc.

Ngoai cu phap, con kiem hai thu de sai khi sua template:
  - `{% extends %}` tro tới file khong ton tai
  - `{% include %}` tro tới file khong ton tai
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web" / "templates"

for _l in (sys.stdout, sys.stderr):
    try:
        _l.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def ten_filter() -> list[str]:
    """Ten cac filter Jinja ma app dang khai, doc tu chinh app/main.py.

    Doc bang regex thay vi import `app.main`: import se keo theo FastAPI,
    oracledb, va mo ca ket noi DB - mot cong lint cu phap khong duoc phep
    can nhung thu do.
    """
    import re
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    ds = re.findall(r'tpl\.env\.filters\[[\'"]([a-zA-Z_][\w]*)[\'"]\]', src)
    # `tojson`, `length`... la cua Jinja san; chi can them cai cua app.
    return sorted(set(ds))


def main() -> int:
    try:
        from jinja2 import Environment, FileSystemLoader
        from jinja2 import TemplateNotFound, TemplateSyntaxError
    except ImportError:
        print("lint_tpl: khong co jinja2 - bo qua", file=sys.stderr)
        return 0

    env = Environment(loader=FileSystemLoader(str(TPL)))
    # Khai ham gia cho moi filter cua app, de `get_template` khong bao
    # "No filter named". Chi kiem CU PHAP, khong render.
    #
    # DOC TEN FILTER TU main.py, khong viet cung danh sach o day: viet cung
    # thi them mot filter moi la cong nay bao LOI OAN va chan deploy. Da xay
    # ra that ngay lan dau dung cong nay (filter `so`).
    for ten in ten_filter():
        env.filters[ten] = lambda x, *a, **k: x

    loi: list[str] = []
    n = 0
    for p in sorted(TPL.glob("*.html")):
        n += 1
        try:
            env.get_template(p.name)
        except TemplateSyntaxError as e:
            loi.append("%s dong %s: %s" % (p.name, e.lineno, e.message))
        except TemplateNotFound as e:
            loi.append("%s: khong thay template %r (extends/include sai)"
                       % (p.name, str(e)))
        except Exception as e:                            # noqa: BLE001
            loi.append("%s: %s" % (p.name, e))

    if loi:
        print("lint_tpl: %d/%d template CO LOI" % (len(loi), n))
        for x in loi:
            print("  - " + x)
        return 1
    print("lint_tpl: %d template SACH" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
