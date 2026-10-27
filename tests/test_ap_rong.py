"""Test duong thu ba 'ap rong' cua hai buoc cho (CHO_AP_HET, CHO_LOW_WM).

Phien #10 (2026-10-21, go BC_BH_NHANH_DTHU khoi PMNV2): buoc 6 dung 4 phut
42 giay vi High WM 392.077.534 dung duoi moc 403.457.593 - luong vang, khong
co giao dich moi nao cua 2 bang trong luong. Nguoi dung phai tu COMMIT o nguon
cho qua (va #7, #8 truoc do cung vay o buoc CHO_LOW_WM). So lieu trong file
nay la so lieu THAT doc tu server luc do.

Khong can Oracle, khong can ggsci: gg.detail / gg.status_of / ora deu gia.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as dbmod                                   # noqa: E402
from app import wizard as wz                                  # noqa: E402
from app.adapters import gg as ggmod                          # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


MOC = 403457593            # SCN ETPMNV2 da doc toi luc dung (buoc 3, phien #10)
WM = "392077534"           # Low = High WM cua RPMNV2 suot 4 phut 42 giay
SCN_ET = "405377750"       # checkpoint SCN cua ETPMNV2 - tang theo redo ca DB

# Hang that cua v$gg_apply_coordinator / v$gg_apply_server luc RPMNV2 ranh
RANH = {"state": "IDLE", "total_received": 0, "total_applied": 0,
        "total_ignored": 0, "total_errors": 0, "unassigned_complete_txns": 0,
        "so_server": 4, "server_ban": 0}


class _Cfg:
    streams = {"PMNV2": {"extract": "ETPMNV2", "replicat": "RPMNV2", "trail": "pa"}}

    def __init__(self, tmp: Path):
        self.gg_home = tmp

    def param_file(self, ten):
        return self.gg_home / "dirprm" / f"{ten.lower()}.prm"


class _Ora:
    def __init__(self, hang=None, loi=None):
        self.hang, self.loi, self.goi = hang, loi, 0

    async def one(self, sql, **b):
        self.goi += 1
        assert b == {"a": "OGG$RPMNV2"}, b
        assert "v$gg_apply_coordinator" in sql and "v$gg_apply_server" in sql
        if self.loi:
            raise RuntimeError(self.loi)
        return self.hang


class _St:
    def __init__(self, chay=True):
        self.is_running = chay
        self.status = "RUNNING" if chay else "STOPPED"
        self.lag_s = 0


class _DongHo:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _trail(tmp: Path) -> Path:
    """pa000000001 = file chua moc (RBA 2249); pa000000002 = file Extract mo
    sau khi chay lai, Replicat da doc het (1404 byte - chi header)."""
    d = tmp / "dirdat"
    d.mkdir(exist_ok=True)
    (d / "pa000000001").write_bytes(b"x" * 2249)
    f2 = d / "pa000000002"
    f2.write_bytes(b"x" * 1404)
    return f2


def _phien(kind: str, ora, *, ctx=None, maxscn=None):
    tmp = Path(tempfile.mkdtemp())
    f2 = _trail(tmp)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(dbmod.SCHEMA)
    con.execute(
        "INSERT INTO wizard_run(kind,state,src_owner,src_table,tgt_owner,tgt_table,"
        "stream,created_at,created_by,updated_at,cur_step,maxscn,ctx_json)"
        " VALUES (?,'OPEN','KT_BSH','B','ODS','B','PMNV2',1,'t',1,1,?,?)",
        (kind, maxscn, json.dumps(ctx or {})))
    w = wz.Wizard(_Cfg(tmp), con, ora, None, None)
    return w, con, w.lay(1), tmp, f2


def _gia_gg(rep, et=None, chay=True):
    """rep / et: dict hoac ham() -> dict, tra cho detail cua Replicat / Extract."""
    goc = (ggmod.detail, ggmod.status_of)

    async def detail(cfg, proc):
        v = et if proc.startswith("ET") else rep
        return dict(v() if callable(v) else v)

    async def status_of(cfg, proc):
        return _St(chay)
    ggmod.detail, ggmod.status_of = detail, status_of
    return lambda: (setattr(ggmod, "detail", goc[0]), setattr(ggmod, "status_of", goc[1]))


def _chay_cho(ham, run, so_lan=8):
    """Thay cho.cho_den: goi do() toi da so_lan, dong ho tien 10s moi lan."""
    dh = _DongHo()
    goc_cho, goc_mono = wz.cho.cho_den, wz.time.monotonic

    async def cho_gia(do, tran_s, nhan):
        cuoi = ""
        for _ in range(so_lan):
            xong, cuoi = await do()
            if xong:
                return {"xong": True, "giay": 0, "cuoi": cuoi, "output": [cuoi]}
            dh.t += 10
        return {"xong": False, "giay": 1200, "cuoi": cuoi, "output": [cuoi]}
    wz.cho.cho_den, wz.time.monotonic = cho_gia, dh
    try:
        return asyncio.run(ham(run, {}))
    finally:
        wz.cho.cho_den, wz.time.monotonic = goc_cho, goc_mono


def _moc_go(tmp: Path) -> dict:
    return {"moc_go": {"scn": MOC, "file": str(tmp / "dirdat" / "pa000000001"),
                       "seqno": 1, "size": 2249}}


def _rep(f2: Path, rba="1404"):
    return {"low_wm": WM, "high_wm": WM, "read_file": str(f2), "rba": rba}


def _go_buoc6(ora, *, rep=None, et=None, chay=True):
    w, con, run, tmp, f2 = _phien("GO_BANG", ora)
    con.execute("UPDATE wizard_run SET ctx_json=? WHERE id=1", (json.dumps(_moc_go(tmp)),))
    run = w.lay(1)
    tra = _gia_gg(rep or _rep(f2), et or {"read_scn": SCN_ET}, chay)
    try:
        return w, con, _chay_cho(w._b_cho_ap_het, run)
    finally:
        tra()


# ===================================================== buoc 6 CHO_AP_HET
@test
def go_buoc6_luong_VANG_qua_bang_ap_rong_sau_30s_KHONG_can_commit():
    """Dung so lieu that phien #10 - truoc day cho mai toi khi co commit."""
    ora = _Ora(dict(RANH))
    w, con, r = _go_buoc6(ora)
    assert "đã áp rỗng" in r["msg"] and "30s/30s" in r["msg"], r["msg"]
    assert "405,377,750 > mốc 403,457,593" in r["msg"], r["msg"]
    assert json.loads(w.lay(1)["ctx_json"])["ap_rong"]["mx"] == MOC
    assert "moc_go" in json.loads(w.lay(1)["ctx_json"]), "ghi de mat moc_go"


@test
def go_buoc6_KHONG_qua_khi_inbound_server_chua_ranh():
    hong = {"apply server dang ap": {"server_ban": 1},
            "giao dich cho giao": {"unassigned_complete_txns": 1},
            "nhan nhieu hon xu ly": {"total_received": 3, "total_applied": 2},
            "coordinator dang ap": {"state": "APPLYING"},
            "khong co apply server": {"so_server": 0}}
    for ten, sua in hong.items():
        try:
            _go_buoc6(_Ora(dict(RANH, **sua)))
        except wz.WizardError:
            continue
        raise AssertionError(f"cho qua khi {ten}")


@test
def go_buoc6_KHONG_qua_khi_KHONG_doc_duoc_inbound_server():
    for ora in (None, _Ora(None), _Ora(loi="ORA-00942: table or view does not exist")):
        try:
            _go_buoc6(ora)
        except wz.WizardError as e:
            assert "chưa áp hết" in str(e), str(e)
            continue
        raise AssertionError(f"cho qua khi khong xem duoc inbound server ({ora})")


@test
def go_buoc6_KHONG_qua_khi_Extract_CHUA_doc_redo_qua_moc():
    for scn in (str(MOC), "403000000", None):
        ora = _Ora(dict(RANH))
        try:
            _go_buoc6(ora, et={"read_scn": scn})
        except wz.WizardError:
            assert ora.goi == 0, "Extract chua qua moc ma van hoi DB dich"
            continue
        raise AssertionError(f"cho qua khi Extract moi doc toi {scn}")


@test
def go_buoc6_KHONG_qua_khi_Replicat_KHONG_chay():
    try:
        _go_buoc6(_Ora(dict(RANH)), chay=False)
    except wz.WizardError:
        return
    raise AssertionError("cho qua khi Replicat da dung")


@test
def go_buoc6_KHONG_qua_khi_trail_CON_CHAY():
    """Luong ban: moi lan hoi Replicat doc toi mot cho moi -> khong bao gio
    dung yen du 30s, du inbound server tung luc IDLE."""
    tmp_f: dict = {}
    n = {"i": 0}

    def rep():
        n["i"] += 1
        f2 = tmp_f["f2"]
        f2.write_bytes(b"x" * (1404 + 100 * n["i"]))
        return _rep(f2, str(1404 + 100 * n["i"]))
    w, con, run, tmp, f2 = _phien("GO_BANG", _Ora(dict(RANH)))
    tmp_f["f2"] = f2
    con.execute("UPDATE wizard_run SET ctx_json=? WHERE id=1", (json.dumps(_moc_go(tmp)),))
    tra = _gia_gg(rep, {"read_scn": SCN_ET})
    try:
        _chay_cho(w._b_cho_ap_het, w.lay(1))
    except wz.WizardError:
        return
    finally:
        tra()
    raise AssertionError("cho qua khi trail con du lieu moi")


@test
def go_buoc6_KHONG_qua_khi_Replicat_CHUA_doc_qua_moc_trail():
    w, con, run, tmp, f2 = _phien("GO_BANG", _Ora(dict(RANH)))
    f1 = tmp / "dirdat" / "pa000000001"
    f2.unlink()                            # chua co file moi: Replicat dang o file moc
    f1.write_bytes(b"x" * 1000)
    con.execute("UPDATE wizard_run SET ctx_json=? WHERE id=1", (json.dumps(_moc_go(tmp)),))
    tra = _gia_gg(_rep(f1, "1000"), {"read_scn": SCN_ET})
    try:
        _chay_cho(w._b_cho_ap_het, w.lay(1))
    except wz.WizardError:
        return
    finally:
        tra()
    raise AssertionError("cho qua khi Replicat moi doc toi RBA 1000 < moc 2249")


# ======================================================== buoc 8 BO_MAP
ET_PRM = "EXTRACT ETPMNV2\nEXTTRAIL /u01/app/ogg/dirdat/pa\nTABLE KT_BSH.A;\n"
REP_PRM = ("REPLICAT RPMNV2\nMAP KT_BSH.A, TARGET ODS.A, COLMAP (USEDEFAULTS);\n"
           "MAP KT_BSH.B, TARGET ODS.B, KEYCOLS (X), COLMAP (USEDEFAULTS);\n")


def _bo_map(ap_rong_mx):
    w, con, run, tmp, f2 = _phien("GO_BANG", None)
    ctx = _moc_go(tmp)
    if ap_rong_mx is not None:
        ctx["ap_rong"] = {"mx": ap_rong_mx, "luc": 1792544955, "mo_ta": "IDLE"}
    con.execute("UPDATE wizard_run SET ctx_json=? WHERE id=1", (json.dumps(ctx),))
    (tmp / "dirprm").mkdir()
    (tmp / "dirprm" / "etpmnv2.prm").write_text(ET_PRM, encoding="utf-8")
    (tmp / "dirprm" / "rpmnv2.prm").write_text(REP_PRM, encoding="utf-8")
    ghi = []

    async def ghi_gia(run, proc, moi, ts, nhan, et_txt):
        ghi.append(moi)
        return {"msg": "ghi", "output": []}
    w._ghi_param_bo = ghi_gia
    # Replicat DA DUNG (buoc 7): WM van dung o 392M, vi tri qua moc trail
    tra = _gia_gg(_rep(f2), {"read_scn": SCN_ET}, chay=False)
    goc_proc = set(ggmod.PROCESSES)
    ggmod.dang_ky_process("ETPMNV2", "RPMNV2")
    try:
        return asyncio.run(w._b_bo_map(w.lay(1), {})), ghi
    finally:
        tra()
        ggmod.PROCESSES.clear()
        ggmod.PROCESSES.update(goc_proc)


@test
def bo_MAP_NHAN_bang_chung_ap_rong_cua_buoc6_DUNG_moc():
    r, ghi = _bo_map(MOC)
    assert "chứng minh áp rỗng" in r["output"][0], r["output"]
    assert ghi and "KT_BSH.B" not in ghi[0] and "KT_BSH.A" in ghi[0], ghi


@test
def bo_MAP_TU_CHOI_khi_KHONG_co_bang_chung_hoac_bang_chung_cua_moc_KHAC():
    for mx in (None, MOC - 1):
        try:
            _bo_map(mx)
        except wz.WizardError as e:
            assert "chưa áp hết" in str(e), str(e)
            continue
        raise AssertionError(f"bo MAP voi bang chung ap rong mx={mx}")


# ============================================= CHO_LOW_WM + BO_HANDLECOLLISIONS
@test
def cho_low_wm_luong_VANG_qua_bang_ap_rong_sau_30s():
    """Phien #7 / #8: buoc 25 cung dung o day cho toi khi co commit."""
    w, con, run, tmp, f2 = _phien("ADD_TABLE", _Ora(dict(RANH)), maxscn=MOC)
    tra = _gia_gg(_rep(f2), {"read_scn": SCN_ET})
    try:
        r = _chay_cho(w._b_cho_low_wm, run)
    finally:
        tra()
    assert "đã áp rỗng" in r["msg"], r["msg"]
    assert json.loads(w.lay(1)["ctx_json"])["ap_rong"]["mx"] == MOC


@test
def cho_low_wm_luong_BAN_van_qua_ngay_bang_low_wm_KHONG_hoi_DB_dich():
    ora = _Ora(dict(RANH))
    w, con, run, tmp, f2 = _phien("ADD_TABLE", ora, maxscn=MOC)
    tra = _gia_gg({"low_wm": str(MOC + 5), "high_wm": str(MOC + 9),
                   "read_file": str(f2), "rba": "1"}, {"read_scn": SCN_ET})
    try:
        r = _chay_cho(w._b_cho_low_wm, run, so_lan=1)
    finally:
        tra()
    assert "403,457,598" in r["msg"] and ora.goi == 0, (r["msg"], ora.goi)


def _bo_hc(ctx):
    w, con, run, tmp, f2 = _phien("ADD_TABLE", None, maxscn=MOC, ctx=ctx)
    goc_read, goc_bo = wz.paramsmod.read, wz.paramsmod.bo_handlecollisions
    wz.paramsmod.read = lambda cfg, ten: type("P", (), {"text": "MAP x;"})()
    wz.paramsmod.bo_handlecollisions = lambda text, o, t: text
    tra = _gia_gg(_rep(f2), {"read_scn": SCN_ET}, chay=False)
    try:
        return asyncio.run(w._b_bo_handlecollisions(w.lay(1), {}))
    finally:
        tra()
        wz.paramsmod.read, wz.paramsmod.bo_handlecollisions = goc_read, goc_bo


@test
def bo_HANDLECOLLISIONS_NHAN_bang_chung_ap_rong_DUNG_moc_nap():
    r = _bo_hc({"ap_rong": {"mx": MOC, "luc": 1, "mo_ta": "IDLE"}})
    assert "không có chế độ chống trùng" in r["msg"], r


@test
def bo_HANDLECOLLISIONS_TU_CHOI_bang_chung_cua_moc_KHAC():
    for ctx in ({}, {"ap_rong": {"mx": MOC + 1, "luc": 1, "mo_ta": ""}}):
        try:
            _bo_hc(ctx)
        except wz.WizardError as e:
            assert "chưa vượt" in str(e), str(e)
            continue
        raise AssertionError(f"bo chong trung voi ctx={ctx}")


# ============================================================== cau truc
@test
def apply_server_INACTIVE_KHONG_tinh_la_ban():
    """So lieu that 2026-10-21: OGG$RDTHU server 1 va OGG$RPMNVODS server 2
    INACTIVE suot nhieu phut trong khi so nhan = so ap."""
    sql = " ".join(wz.SQL_INBOUND.split())
    assert "s.state NOT IN ('IDLE', 'INACTIVE')" in sql, sql


@test
def MOI_cho_doc_moc_nap_deu_co_duong_ap_rong():
    """Moi handler goi _qua_moc_nap (cho hoac chot kiem lai) PHAI co duong ap
    rong - thieu mot cho la luong vang lai ket o do, nhu #7/#8/#10."""
    src = inspect.getsource(wz.Wizard)
    ten = []
    for khoi in src.split("\n    async def ")[1:]:
        if "self._qua_moc_nap(" in khoi:
            ten.append(khoi.split("(")[0])
            assert ("self._qua_ap_rong(" in khoi
                    or "self._da_ap_rong_truoc(" in khoi), khoi.split("(")[0]
    assert sorted(ten) == ["_b_bo_handlecollisions", "_b_bo_map",
                           "_b_cho_ap_het", "_b_cho_low_wm"], ten


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
