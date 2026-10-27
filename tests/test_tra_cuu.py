"""Test menu tra cuu bang: bang dang o luong / Extract / Replicat nao.

Doc thang file tham so cua MOI luong (ke ca luong LOG). Khong can Oracle.
Kem mot lan render template that de bat loi cu phap Jinja.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as dbmod                                   # noqa: E402
from app import registry as rg                                # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class _Cfg:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.streams = {
            "PMNV3": {"extract": "ETPMNV3", "replicat": "RPMNV3"},
            "PMNV2": {"extract": "ETPMNV2", "replicat": "RPMNV2"},
            "LOG": {"extract": "ETLOG", "replicat": "RLOG", "loai": "log"},
        }
        self._ghi("etpmnv3", "TABLE KT_BSH.BH_XE_GCN_TV;\nTABLE KT_BSH.BH_MAT_MAP;\n")
        self._ghi("rpmnv3", "MAP KT_BSH.BH_XE_GCN_TV, TARGET ODS.BH_XE_GCN_TV;\n")
        self._ghi("etpmnv2", "TABLE KT_BSH.KT_BCHQ;\n")
        self._ghi("rpmnv2", "MAP KT_BSH.KT_BCHQ, TARGET ODS.KT_BCHQ, KEYCOLS (MA_DVI, MA), "
                            "HANDLECOLLISIONS;\nMAP KT_BSH.BH_CHI_MAP, TARGET ODS.BH_CHI_MAP;\n")
        self._ghi("etlog", "TABLE KT_BSH.KT_BCHQ;\n")
        self._ghi("rlog", "MAP KT_BSH.KT_BCHQ, TARGET ODS.KT_BCHQ_LOG;\n")

    def _ghi(self, ten, s):
        (self.tmp / f"{ten}.prm").write_text(s)

    def param_file(self, ten):
        return self.tmp / f"{ten.lower()}.prm"


def _con():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(dbmod.SCHEMA)
    c.execute("INSERT INTO managed_table(src_owner,src_table,tgt_owner,tgt_table,stream,"
              "in_extract,has_map,prepared,handlecollisions,note,updated_at)"
              " VALUES ('KT_BSH','KT_MYBSH_SYNC','ODS','KT_MYBSH_SYNC','PMNV2',0,0,0,0,"
              "'mới đăng ký',1)")
    return c


def _kq():
    return {x["bang"]: x for x in rg.tra_cuu(
        _Cfg(), _con(), ["bh_xe_gcn_tv", "KT_BCHQ", "BH_MAT_MAP", "BH_CHI_MAP",
                         "KT_MYBSH_SYNC", "BH_KHONG_DAU", "KT_BCHQ"])}


@test
def dang_dong_bo_va_chi_ra_dung_extract_replicat():
    x = _kq()["BH_XE_GCN_TV"]
    assert x["muc"] == "ok", x
    assert [e["extract"] for e in x["extract"]] == ["ETPMNV3"]
    assert x["replicat"][0]["replicat"] == "RPMNV3"


@test
def bang_o_ca_luong_thuong_lan_luong_LOG_deu_hien():
    x = _kq()["KT_BCHQ"]
    assert x["muc"] == "ok"
    assert {e["extract"] for e in x["extract"]} == {"ETPMNV2", "ETLOG"}, x["extract"]
    r = {m["replicat"]: m for m in x["replicat"]}
    assert r["RPMNV2"]["keycols"] == "MA_DVI,MA" and r["RPMNV2"]["hc"] == 1
    assert r["RLOG"]["tgt_table"] == "KT_BCHQ_LOG" and r["RLOG"]["loai"] == "log"


@test
def TABLE_ma_khong_MAP_la_loi_do():
    assert _kq()["BH_MAT_MAP"]["muc"] == "bad"
    assert "KHÔNG có MAP" in _kq()["BH_MAT_MAP"]["ket_luan"]


@test
def MAP_ma_khong_TABLE_la_loi_do():
    assert _kq()["BH_CHI_MAP"]["muc"] == "bad"


@test
def da_dang_ky_chua_vao_luong_va_chua_co_gi():
    k = _kq()
    assert k["KT_MYBSH_SYNC"]["muc"] == "warn"
    assert k["BH_KHONG_DAU"]["muc"] == "muted"
    assert len(k) == 6                       # KT_BCHQ lap -> mot dong


@test
def template_render_duoc():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(ROOT / "web" / "templates")))
    src = (ROOT / "web" / "templates" / "tra_cuu.html").read_text(encoding="utf-8")
    body = src.split("{% block body %}", 1)[1].rsplit("{% endblock %}", 1)[0]
    kq = list(_kq().values())
    kq[0].update(co_nguon=True, trandata=True, co_dich=True, bang_dich=kq[0]["bang"])
    html = env.from_string(body).render(q="x", kq=kq, loi_ora="", sai=["X;Y"],
                                        qua_tran=False)
    assert "BH_XE_GCN_TV" in html and "ETPMNV3" in html and "HANDLECOLLISIONS" in html


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
