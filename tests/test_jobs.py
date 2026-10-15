"""Test job roi ra.

`con_song()` la ham quyet dinh mot job dang RUNNING co bi danh dau KILLED
khi app khoi dong lai hay khong. Sai o day co hau qua that: mot dot nap dang
chay bi bao KILLED oan thi UI noi sai, VA pre-flight `khong_co_job_khac` cho
phep mot dot nap THU HAI khoi dong. Da xay ra that trong phien xay dung.

Nhanh "con song = True" duoc chung minh bang dien tap that (job #6: spawn,
restart app giua luc chay, job giu RUNNING roi tu ve SUCCEEDED). O day test
cac nhanh TU CHOI, vi chung khong the dien tap duoc het.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import jobs                                           # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _job(**kw):
    p = {k: v for k, v in kw.items() if k != "id"}
    return {"id": kw.get("id", 1), "params_json": json.dumps(p)}


def _nap_job_done():
    """Nap `tools/job_done.py` nhu mot module de test CHINH doi tuong that.

    No khong nam trong package `app` nen khong import thang duoc.
    """
    import importlib.util
    p = ROOT / "tools" / "job_done.py"
    spec = importlib.util.spec_from_file_location("job_done_mod", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@test
def khong_co_params_json_thi_coi_la_chet():
    assert jobs.con_song({"id": 1, "params_json": None}) is False
    assert jobs.con_song({"id": 1, "params_json": ""}) is False
    assert jobs.con_song({"id": 1, "params_json": "khong-phai-json"}) is False


@test
def job_khong_detach_thi_coi_la_chet():
    """Job verify chay trong tien trinh web: restart app la chet thuc su."""
    assert jobs.con_song(_job(detach=False, pid=os.getpid())) is False
    assert jobs.con_song(_job(pid=os.getpid())) is False          # thieu detach


@test
def thieu_pid_thi_coi_la_chet():
    assert jobs.con_song(_job(detach=True)) is False
    assert jobs.con_song(_job(detach=True, pid=0)) is False


@test
def pid_khong_ton_tai_thi_coi_la_chet():
    # 4194304 vuot pid_max mac dinh cua Linux (4194304) nen chac chan khong co
    assert jobs.con_song(_job(detach=True, pid=4194304)) is False


@test
def PID_REUSE_pid_song_nhung_cmdline_khong_khop_thi_coi_la_chet():
    """Chi kiem pid la KHONG DU.

    Tren may 16 vCPU voi nhieu tien trinh Oracle, pid quay vong khong phai
    chuyen hiem. Mot pid duoc dung lai boi tien trinh khac ma ta tuong la job
    cua minh thi se KHONG danh dau KILLED mot job da chet - va cai gia la mot
    job treo RUNNING vinh vien, chan moi lan nap ve sau.

    Tien trinh dang chay test nay co pid that nhung cmdline cua no khong chua
    `job_1.sh`, nen phai tra False.
    """
    if not Path("/proc").is_dir():
        return                      # khong phai Linux: bo qua ca nay
    assert jobs.con_song(_job(id=1, detach=True, pid=os.getpid())) is False


@test
def wrapper_khong_chua_mat_khau():
    """Wrapper bash KHONG duoc chua mat khau.

    `sqlplus user/pw@dsn` de mat khau lo ra trong `ps` cho MOI user tren may.
    Cach dung: `job_connect.py` doc secrets.ini roi in cau `connect` ra
    stdout, pipe vao `sqlplus -S /nolog`. Mat khau chi ton tai trong mot pipe.
    """
    import tempfile

    class FakeCfg:
        oracle_home = Path("/u01/app/oracle/product/19.3.0.0/dbhome_1")

        def __init__(self, root):
            self.scripts_dir = Path(root) / "scripts"
            self.log_dir = Path(root) / "logs"

        @staticmethod
        def subprocess_env():
            return {"ORACLE_HOME": "/u01/app/oracle/x", "NLS_LANG": "AL32UTF8"}

    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        p = jobs._wrapper(cfg, 42, "ggadm_load", Path("/tmp/x.sql"))
        body = p.read_text(encoding="utf-8")
        assert "/nolog" in body, "phai dung `sqlplus -S /nolog`"
        assert "job_connect.py" in body, "mat khau phai di qua pipe"
        assert "job_done.py" in body, "chinh tien trinh job phai tu cap nhat"
        # Khong duoc co dang user/password@dsn trong wrapper
        for xau in ("ggadm_load/", "PASSWORD", "password ="):
            assert xau not in body, f"wrapper khong duoc chua {xau!r}"
        # Phai ghi log ra file de DBA tail -f duoc
        assert "job_42.log" in body
        # env dung rieng, khong ke thua
        assert "export ORACLE_HOME=" in body
        # Quyen file chi kiem tren Linux: tren Windows (may dev) chmod POSIX
        # khong co hieu luc nen assertion se that bai oan.
        if os.name == "posix":
            assert oct(p.stat().st_mode & 0o777) == "0o700", \
                oct(p.stat().st_mode & 0o777)


@test
def job_done_doc_dung_cac_moc():
    """`job_done.py` phai doc duoc MAXSCN/SO_DONG tu log."""
    # Nap CHINH doi tuong RE_MOC thay vi boc regex ra bang mot regex khac:
    # cach boc do vo ngay khi RE_MOC duoc viet thanh hai chuoi noi tiep (da
    # xay ra khi them moc `SO_DONG_<BANG>` cho lo nhieu bang), va no kiem
    # mot BAN SAO chu khong phai cai that dang chay.
    pat = _nap_job_done().RE_MOC
    log = ("bat dau\nMAXSCN=160079202\nSO_DONG=7432990\n"
           "SO_INDEX=3\nxong\n")
    moc = {x.group(1): int(x.group(2)) for x in pat.finditer(log)}
    assert moc == {"MAXSCN": 160079202, "SO_DONG": 7432990, "SO_INDEX": 3}, moc


@test
def job_done_doc_duoc_moc_tung_bang_cua_lo():
    """Lo nhieu bang: script in `SO_DONG_<BANG>=` cho tung bang.

    Thieu cac moc nay thi buoc kiem chung chi doi chieu duoc TONG so dong,
    va mot bang nhan 0 dong se lot qua im lang.
    """
    pat = _nap_job_done().RE_MOC
    log = ("SO_DONG_CN_CH=1294304\nSO_DONG_CN_CT=1516157\n"
           "SO_DONG_XD_MA_CTR=0\nMAXSCN=163531599\nSO_DONG=2810461\n"
           # Dong nhieu co dau `=` KHONG duoc thanh moc
           "ket qua = khong ro\n  v_nap := 5\n")
    moc = {x.group(1): int(x.group(2)) for x in pat.finditer(log)}
    assert moc == {"SO_DONG_CN_CH": 1294304, "SO_DONG_CN_CT": 1516157,
                   "SO_DONG_XD_MA_CTR": 0, "MAXSCN": 163531599,
                   "SO_DONG": 2810461}, moc


@test
def job_done_coi_ORA_la_that_bai_du_ma_thoat_bang_0():
    """Ma thoat 0 ma van co ORA- thi KHONG duoc coi la thanh cong.

    `WHENEVER SQLERROR EXIT FAILURE` co the bi mot khoi EXCEPTION bat mat,
    luc do sqlplus tra 0 trong khi script that bai.
    """
    import re
    src = (ROOT / "tools" / "job_done.py").read_text(encoding="utf-8")
    m = re.search(r'RE_ORA = re\.compile\(r"([^"]+)"\)', src)
    assert m, "khong thay RE_ORA"
    pat = re.compile(m.group(1))
    for l in ("ORA-01555: snapshot too old", "SP2-0734: unknown command",
              "PLS-00201: identifier must be declared"):
        assert pat.search(l), l
    assert not pat.search("da nap 7432990 dong")
    assert "and not loi" in src, \
        "phai yeu cau CA rc==0 VA khong co dong ORA- moi cho SUCCEEDED"


# =====================================================================
# Do song song theo SO DONG, khong phai mot con so co dinh.
#
# Do duoc tren server 2026-10-14: sga_target 21.056 MB +
# pga_aggregate_limit 14.000 MB tren may 31 GB RAM = 35 GB tren 31 GB.
# Swap 4 GB DAY 100% va he van day ra ~9 MB/phut. `PARALLEL 8` cho mot
# bang 3.355 dong vua lam nang them suc ep, vua khong nhanh hon.
# =====================================================================

@test
def do_song_song_theo_kich_thuoc_bang():
    from app.wizard_index import IndexMixin as M

    # Bang danh muc: KHONG song song
    for n in (0, 1, 1174, 3355, 13592, 100_000):
        assert M.do_song_song(n) == 1, n
    # Bang trung binh
    for n in (100_001, 2_838_682, 4_099_866, 5_000_000):
        assert M.do_song_song(n) == 4, n
    # Bang lon (BC_*, BH_*)
    for n in (5_000_001, 60_000_000, 111_224_665, 256_142_887):
        assert M.do_song_song(n) == 8, n


@test
def HT_MA_KVUC_phai_khong_song_song():
    """Bang vua them vao luong: 3.355 dong -> NOPARALLEL.

    Lan chay that dau tien dung PARALLEL 8 cho bang nay - do la sai.
    """
    from app.wizard_index import IndexMixin as M
    assert M.do_song_song(3355) == 1


@test
def script_rebuild_bang_nho_dung_NOPARALLEL():
    import json
    from app.wizard_index import IndexMixin

    class W(IndexMixin):
        # `_cac_bang` song o lop `Wizard`; stub nay chi tron `IndexMixin` nen
        # phai tu cung cap. Phien MOT bang = list mot phan tu.
        def _cac_bang(self, run):
            return [run["src_table"]] if run.get("src_table") else ["X"]

    run = {"id": 9, "tgt_owner": "ODS", "tgt_table": "HT_MA_KVUC",
           "src_table": "HT_MA_KVUC",
           "ctx_json": json.dumps({"n_src": 3355})}
    sql = W()._sql_index(run, ["IX_HT_MA_KVUC_CAP"], "REBUILD")
    assert "NOPARALLEL" in sql, sql
    assert "PARALLEL 8" not in sql, sql
    assert "PARALLEL 4" not in sql, sql
    assert "3,355 dong -> do song song 1" in sql, sql

    # Bang lon thi van PARALLEL 8 + NOPARALLEL ngay sau
    run2 = dict(run, tgt_table="BC_BH_HD_DTBH",
                ctx_json=json.dumps({"n_src": 111_224_665}))
    sql2 = W()._sql_index(run2, ["BC_BH_HD_DTBH_I2"], "REBUILD")
    assert "PARALLEL 8;" in sql2, sql2
    assert "NOPARALLEL;" in sql2, "phai reset NOPARALLEL ngay sau REBUILD"
