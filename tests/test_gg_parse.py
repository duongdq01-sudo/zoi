"""Test parser ggsci bang FIXTURE OUTPUT THAT, khong mock bang tay.

Ca quan trong nhat trong ca file: `info all` vs `info <proc>, detail`.
Day la cho khoa vinh vien cam bay 26 - cai cam bay da lam vong cho drain
chay het so lan vo ich tren mot he hoan toan binh thuong.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).resolve().parent / "fixtures"

from app.adapters import gg                                  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _lines(name: str) -> list[str]:
    return [l.rstrip() for l in (FIX / name).read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ===================================================================== info all
@test
def info_all_doc_du_7_process():
    ps = gg.parse_info_all(_lines("info_all.txt"))
    assert len(ps) == 7, f"ky vong 7 process, duoc {len(ps)}: {[p.name for p in ps]}"
    assert {p.name for p in ps} == {"MGR", "ETDTHU", "ETHT", "ETPMNV",
                                    "RDTHU", "RHT", "RPMNVODS"}


@test
def info_all_manager_khong_co_lag():
    ps = {p.name: p for p in gg.parse_info_all(_lines("info_all.txt"))}
    m = ps["MGR"]
    assert m.program == "MANAGER" and m.status == "RUNNING"
    assert m.lag_s is None and m.since_chkpt_s is None


@test
def info_all_doc_dung_lag():
    ps = {p.name: p for p in gg.parse_info_all(_lines("info_all.txt"))}
    assert ps["RHT"].lag_s == 0
    assert ps["RHT"].since_chkpt_s == 9
    # 00:07:45 = 465 giay - dung gai lag that da do duoc cua RPMNVODS
    assert ps["RPMNVODS"].lag_s == 465, ps["RPMNVODS"].lag_s
    assert all(p.is_running for p in ps.values())


@test
def CAM_BAY_26_detail_khong_duoc_parse_thanh_trang_thai():
    """Dong `Last Started` cua `info detail` KHONG duoc tro thanh ProcState.

    Neu regex khong neo cuoi dong thi dong
        REPLICAT   RHT   Last Started 2026-10-13 17:20   Status RUNNING
    se cho status = "RHT" va cot thu 3 = "Last", roi so sanh lag voi
    "00:00:00" khong bao gio bang -> vong cho drain vo hieu.
    """
    ps = gg.parse_info_all(_lines("info_detail_rht.txt"))
    assert ps == [], f"detail KHONG duoc parse ra process nao, duoc: {ps}"
    for p in ps:
        assert p.status not in ("Last", "Started", "RHT")


@test
def detail_doc_dung_watermark_va_rba():
    """Kiem cac regex cua detail() tren fixture that.

    Luu y High WM 150502846 chinh la SCN cua lan verify 18/18 that.
    """
    import re
    txt = (FIX / "info_detail_rht.txt").read_text(encoding="utf-8")
    hi = re.search(r"High Watermark CSN value:\s*(\d+)", txt)
    lo = re.search(r"Low Watermark CSN value:\s*(\d+)", txt)
    rba = re.search(r"RBA\s+(\d+)", txt)
    assert hi and hi.group(1) == "150502846"
    assert lo and lo.group(1) == "150502811"
    assert rba and rba.group(1) == "1829447"
    # Low < High luon dung - va day la ly do khong bao gio cho chung gap nhau
    assert int(lo.group(1)) < int(hi.group(1))


# =================================================================== blocklist
@test
def blocklist_chan_du_cac_lenh_nguy_hiem():
    phai_chan = [
        "alter replicat RHT, begin now",
        "ALTER EXTRACT ETHT, EXTSEQNO 4, EXTRBA 0",
        "alter extract ETHT, extrba 0",
        "cleanup replicat RHT",
        "delete tracetable",
        "add rmttrail /tmp/x, extract ETHT",
        "kill replicat RHT",
        "stop mgr",
        "stop er *",
        "start er *",
        "shell rm -rf /",
        "! ls",
        "obey /tmp/script.oby",
        "send replicat RHT, forcestop",
        "set editor vi",
        "edit param rht",
        "create subdirs",
        "stop replicat RP*",
        "register extract ETTEST database",
        "unregister extract ETHT database",
        "add extract ETTEST, integrated tranlog, begin now",
        "delete extract ETHT",
        "add replicat RTEST, integrated, exttrail ./dirdat/tt",
        "delete replicat RHT",
        "add exttrail ./dirdat/tt, extract ETTEST",
        "add checkpointtable OGG_BSH.RTEST",
        "delete checkpointtable OGG_BSH.RHT",
        "delete trandata KT_BSH.HT_NSD_PVI",
    ]
    for cmd in phai_chan:
        try:
            gg.check_script(cmd)
        except gg.Blocked:
            continue
        raise AssertionError(f"KHONG bi chan ma phai bi chan: {cmd!r}")


@test
def blocklist_khong_chan_lenh_hop_le():
    phai_qua = [
        "info all",
        "stop replicat RHT",
        "start replicat RHT",
        "stop extract ETHT",
        "start extract ETHT",
        "dblogin useridalias bshc_ogg\ninfo RHT, detail",
        "stats replicat RHT, latest",
        "view param rht",
        "dblogin useridalias pmnv_ogg\ninfo trandata KT_BSH.HT_NSD_PVI",
        "dblogin useridalias pmnv_ogg\nadd trandata KT_BSH.HT_NSD_PVI",
    ]
    for cmd in phai_qua:
        gg.check_script(cmd)       # khong duoc raise


@test
def check_arg_chan_ky_tu_tach_lenh():
    """ggsci tach lenh theo DONG: mot \\n trong ten bang la LENH THU HAI.

    Day la injection nguy hiem hon SQL injection o day vi khong ai nghi toi.
    """
    for xau in ["A\nstop mgr", "A\rstop mgr", "A;stop mgr", "A!ls", "\n"]:
        try:
            gg.check_arg(xau)
        except gg.Blocked:
            continue
        raise AssertionError(f"check_arg phai tu choi {xau!r}")
    assert gg.check_arg("HT_NSD_PVI") == "HT_NSD_PVI"


@test
def process_ngoai_enum_bi_tu_choi():
    assert "ETHT" in gg.PROCESSES and "RHT" in gg.PROCESSES
    for xau in ["ETTEST", "RTEST", "etht", "", "*"]:
        assert xau not in gg.PROCESSES, xau


@test
def noise_bi_loc():
    """Dong nhieu cua banner GGSCI bi loc o DUNG MOT CHO (tang adapter)."""
    for l in ["Copyright (c) 1995, 2023, Oracle and/or its affiliates.",
              "Version 21.3.0.0.0 OGGCORE_21.3.0.0.0",
              "post-quantum cryptography",
              "Operating system character set identified as UTF-8."]:
        assert gg.NOISE.search(l), f"phai coi la nhieu: {l!r}"
    assert not gg.NOISE.search("EXTRACT     RUNNING     ETHT        00:00:00")


@test
def trandata_doc_dong_DUNG_khong_doc_OGG_26035():
    """Dong can doc la `Logging of supplemental redo log data is ...`.

    KHONG doc OGG-26035: dong do phan anh tham so DB-level
    `enable_goldengate_replication=TRUE` nen in cho MOI bang trong schema,
    ke ca bang CHUA add TRANDATA. Dem OGG-26035 la cach da cho ra con so sai
    "1.866 bang" phai dinh chinh ve 44.
    """
    bat = "Logging of supplemental redo log data is enabled for table KT_BSH.X"
    tat = "Logging of supplemental redo log data is disabled for table KT_BSH.Y"
    gia = ("INFO OGG-26035  Logical replication for table KT_BSH.Z is ENABLED")
    m = gg.TRANDATA_STATE.search(bat)
    assert m and m.group(1).lower() == "enabled"
    m = gg.TRANDATA_STATE.search(tat)
    assert m and m.group(1).lower() == "disabled"
    assert gg.TRANDATA_STATE.search(gia) is None, \
        "OGG-26035 KHONG duoc coi la bang chung TRANDATA da bat"


@test
def hhmmss_doi_dung():
    assert gg._hhmmss_to_s("00:00:00") == 0
    assert gg._hhmmss_to_s("00:07:45") == 465
    assert gg._hhmmss_to_s("01:00:00") == 3600
    # Chu `Started` phai tra None, khong phai 0 - neu tra 0 thi vong cho
    # drain se tuong da drain xong.
    assert gg._hhmmss_to_s("Started") is None
    assert gg._hhmmss_to_s(None) is None


# =====================================================================
# HOI QUY cho 3 loi phat hien 2026-10-14 khi chay that lan dau.
# Ca ba deu khong the phat hien bang doc code - chi lo ra khi doi chieu
# voi output va ten file THAT tren server.
# =====================================================================

@test
def HOI_QUY_detail_cua_Extract_doc_duoc_seqno_va_rba():
    """Dong EXTTRAIL cua Extract phai cho ra trail/seqno/rba.

    Khong co seqno thi `drain_state` khong biet file trail nao, `trail_bytes`
    thanh None, va dieu kien drain KHONG BAO GIO dung.
    """
    d = gg.parse_detail(_lines("info_detail_etht.txt"))
    assert d.get("trail") == "ht", d
    assert d.get("seqno") == "5", d
    assert d.get("rba") == "199839", d
    assert d.get("trail_max_mb") == "100", d
    assert d.get("read_scn") == "158531200", d


@test
def HOI_QUY_detail_cua_Replicat_khong_bi_dong_EXTTRAIL_ghi_de():
    d = gg.parse_detail(_lines("info_detail_rht.txt"))
    assert d.get("high_wm") == "150502846"
    assert d.get("rba") == "1829447"
    assert d.get("read_file") == "/u01/app/ogg/dirdat/ht000000004"
    assert "seqno" not in d, "Replicat khong co bang Target Extract Trails"


@test
def HOI_QUY_trail_file_tim_dung_file_du_do_rong_chuoi_so_khac_nhau():
    """Loi da gap that: doan 6 chu so (`ht000005`) trong khi ban 21.3 ghi 9
    (`ht000000005`) -> exists() False -> trail_bytes None -> drained KHONG
    BAO GIO True -> verify che do chuan LUON LOI.
    """
    import tempfile
    from pathlib import Path

    class FakeCfg:
        def __init__(self, root):
            self.gg_home = Path(root)

    # a) ban 21.3: 9 chu so
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "dirdat"
        d.mkdir()
        (d / "ht000000004").write_bytes(b"x" * 11)
        (d / "ht000000005").write_bytes(b"x" * 22)
        cfg = FakeCfg(td)
        f = gg.trail_file(cfg, "ht", 5)
        assert f.exists(), f"khong tim thay file 9 chu so: {f}"
        assert f.stat().st_size == 22
        assert gg.trail_file(cfg, "ht", 4).stat().st_size == 11

    # b) ban cu: 6 chu so - cung phai tim duoc
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "dirdat"
        d.mkdir()
        (d / "ht000005").write_bytes(b"y" * 33)
        f = gg.trail_file(FakeCfg(td), "ht", 5)
        assert f.exists() and f.stat().st_size == 33, f

    # c) khong co file nao -> tra duong dan mac dinh 9 chu so, khong raise
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "dirdat").mkdir()
        f = gg.trail_file(FakeCfg(td), "ht", 7)
        assert f.name == "ht000000007", f.name
        assert not f.exists()

    # d) khong duoc bat file cua prefix khac (ht vs htx)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "dirdat"
        d.mkdir()
        (d / "htx000000005").write_bytes(b"z")
        f = gg.trail_file(FakeCfg(td), "ht", 5)
        assert f.name == "ht000000005", \
            f"'htx000000005' co phan du 'x000000005' khong phai so -> khong duoc khop, duoc {f.name}"


@test
def HOI_QUY_alias_suy_ra_tu_loai_process():
    """Hardcode mot alias cho ca Extract lan Replicat la loi.

    Extract doc redo o NGUON, Replicat apply o DICH. Ca 2 DB cung
    SERVICE_NAME=BSHC nen prompt GGSCI hien @BSHC cho ca hai - nhin man hinh
    khong phat hien ra. Chinh ham detail() da mac loi nay.
    """
    for et in ("ETPMNV", "ETHT", "ETDTHU"):
        assert gg.alias_for(et) == gg.ALIAS_SOURCE, et
    for rp in ("RPMNVODS", "RHT", "RDTHU"):
        assert gg.alias_for(rp) == gg.ALIAS_TARGET, rp
    assert gg.ALIAS_SOURCE != gg.ALIAS_TARGET


# =====================================================================
# HOI QUY cho LOI THU TU (2026-10-14, lan chay quiesce that dau tien):
# drain duoc do tren EXTRACT thay vi tren REPLICAT.
#
# Hau qua that: vong cho chay 290 giay khong nhuc nhich tren mot he hoan
# toan binh thuong, va se chay den het 870 giay roi abort - vi lag cua mot
# Extract DA DUNG dong bang o 00:00:01 va KHONG BAO GIO ve 0.
# =====================================================================

def _fake_gg(detail_map: dict, status_map: dict):
    """Thay gg.detail/gg.status_of de test drain_state khong can ggsci."""
    import contextlib

    @contextlib.contextmanager
    def ctx():
        d0, s0 = gg.detail, gg.status_of

        async def fake_detail(cfg, proc):
            return detail_map[proc]

        async def fake_status(cfg, proc):
            return status_map.get(proc)

        gg.detail, gg.status_of = fake_detail, fake_status
        try:
            yield
        finally:
            gg.detail, gg.status_of = d0, s0
    return ctx()


def _drain(detail_map, status_map, root):
    import asyncio
    from pathlib import Path

    class FakeCfg:
        gg_home = Path(root)

    with _fake_gg(detail_map, status_map):
        return asyncio.run(gg.drain_state(FakeCfg(), "ETHT", "RHT", "ht"))


@test
def HOI_QUY_drain_do_tren_REPLICAT_khong_phai_EXTRACT():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "dirdat"
        d.mkdir()
        f = d / "ht000000006"
        f.write_bytes(b"x" * 16335)
        P = str(f)
        R = lambda s: gg.ProcState("REPLICAT", "RHT", "RUNNING", s, 0)

        # CA CHINH: Extract da STOPPED va lag cua no dong bang o 1 giay.
        # Replicat da doc het trail va lag 0 -> PHAI la drained.
        # Code cu doi lag cua EXTRACT ve 0 nen ca nay bi ket vinh vien.
        st = _drain(
            {"ETHT": {"seqno": "6", "rba": "16335"},
             "RHT": {"read_file": P, "rba": "16335"}},
            {"ETHT": gg.ProcState("EXTRACT", "ETHT", "STOPPED", 1, 1),
             "RHT": R(0)}, td)
        assert st["drained"] is True, st
        assert st["vi_sao"] == "da drain", st

        # Replicat con sau mot file trail
        st = _drain(
            {"ETHT": {"seqno": "6", "rba": "16335"},
             "RHT": {"read_file": str(d / "ht000000005"), "rba": "100"}},
            {"RHT": R(0)}, td)
        assert st["drained"] is False
        # Kiem DAU HIEU, khong kiem nguyen van: thong diep la van ban cho
        # nguoi doc va da doi mot lan khi dich sang tieng Viet co dau.
        assert "số" in st["vi_sao"] and "trail" in st["vi_sao"], st["vi_sao"]

        # Replicat dung file nhung chua doc het
        st = _drain(
            {"ETHT": {"seqno": "6", "rba": "16335"},
             "RHT": {"read_file": P, "rba": "13869"}},
            {"RHT": R(0)}, td)
        assert st["drained"] is False
        assert "2,466 byte chưa áp" in st["vi_sao"], st["vi_sao"]

        # Doc het nhung lag chua ve 0
        st = _drain(
            {"ETHT": {"seqno": "6", "rba": "16335"},
             "RHT": {"read_file": P, "rba": "16335"}},
            {"RHT": R(195)}, td)
        assert st["drained"] is False
        assert "chậm" in st["vi_sao"] and "195" in st["vi_sao"], st["vi_sao"]

        # Khong doc duoc checkpoint ca hai ben -> khong duoc bao da drain
        st = _drain({"ETHT": {}, "RHT": {}}, {"RHT": R(0)}, td)
        assert st["drained"] is False, st


# =====================================================================
# PHASE 4 - LOP KHOANH VUNG. Day la phong tuyen an ninh quan trong nhat
# cua ca cong cu, nen no phai co test day du nhat.
#
# Cac lenh REGISTER/UNREGISTER/ADD/DELETE EXTRACT|REPLICAT|EXTTRAIL|
# CHECKPOINTTABLE KHONG chan duoc (wizard NEW_STREAM can chinh chung), nen
# luat thay the phai duoc kiem ky: chi tac dong len ten do CHINH PHIEN DO
# tao ra, va 6 process + 3 trail + 3 checkpoint table dang chay la BAT KHA
# XAM PHAM.
# =====================================================================

@test
def ten_moi_khong_duoc_trung_doi_tuong_dang_chay():
    for xau in ("ETHT", "RHT", "ETPMNV", "RPMNVODS", "ETDTHU", "RDTHU", "MGR"):
        try:
            gg.kiem_ten_moi(xau, "extract")
        except gg.Blocked as e:
            assert "bất khả xâm phạm" in str(e), str(e)
            continue
        raise AssertionError(f"{xau} dang chay ma khong bi tu choi")
    for xau in ("pm", "ht", "dt"):
        try:
            gg.kiem_ten_moi(xau, "trail")
        except gg.Blocked:
            continue
        raise AssertionError(f"trail {xau} dang dung ma khong bi tu choi")


@test
def ten_moi_phai_dung_dinh_dang():
    # ten group: 2-8 ky tu, bat dau bang chu cai
    for xau in ("", "E", "ETQUADAI9", "1ET", "ET-HT", "ET HT", "ET;x"):
        try:
            gg.kiem_ten_moi(xau, "extract")
        except gg.Blocked:
            continue
        raise AssertionError(f"ten {xau!r} khong hop le ma duoc chap nhan")
    assert gg.kiem_ten_moi("ettest", "extract") == "ETTEST"
    assert gg.kiem_ten_moi("ET_BC2", "extract") == "ET_BC2"

    # trail prefix: DUNG 2 ky tu thuong
    for xau in ("t", "ttt", "TT", "t1", "t-"):
        try:
            gg.kiem_ten_moi(xau, "trail")
        except gg.Blocked:
            continue
        raise AssertionError(f"trail prefix {xau!r} khong hop le ma duoc nhan")
    assert gg.kiem_ten_moi("tt", "trail") == "tt"


@test
def pham_vi_TU_CHOI_doi_tuong_dang_chay_KE_CA_khi_duoc_khai_cho_phep():
    """Thu tu kiem khong duoc dao: lop bat kha xam pham TRUOC allowlist.

    Neu dao lai thi mot phien khai `cho_phep={"RHT"}` se xoa duoc Replicat
    that - va day la cach de nhat de pha ca he thong tu trong UI.
    """
    for script in ("delete replicat RHT",
                   "delete extract ETHT",
                   "unregister extract ETPMNV database",
                   "delete checkpointtable OGG_BSH.RHT",
                   "add exttrail ./dirdat/ht, extract ETTEST"):
        try:
            # Co tinh khai CHINH ten do trong danh sach cho phep
            gg.kiem_pham_vi(script, {"RHT", "ETHT", "ETPMNV", "OGG_BSH.RHT",
                                     "HT", "ETTEST"})
        except gg.Blocked as e:
            assert "bất khả xâm phạm" in str(e), str(e)
            continue
        raise AssertionError(f"KHONG chan: {script!r}")


@test
def pham_vi_tu_choi_ten_phien_khong_tao_ra():
    cp = {"ETTEST", "RTEST", "tt", "OGG_BSH.RTEST", "RTEST"}
    for script in ("delete replicat RKHAC",
                   "add extract ETKHAC, integrated tranlog, begin now",
                   "add checkpointtable OGG_BSH.RKHAC",
                   "unregister extract ETKHAC database"):
        try:
            gg.kiem_pham_vi(script, cp)
        except gg.Blocked as e:
            assert "không tạo ra" in str(e) or "bất khả xâm phạm" in str(e), str(e)
            continue
        raise AssertionError(f"KHONG chan ten ngoai phien: {script!r}")


@test
def pham_vi_CHO_QUA_dung_ten_cua_phien():
    cp = {"ETTEST", "RTEST", "tt", "OGG_BSH.RTEST"}
    for script in (
        "add checkpointtable OGG_BSH.RTEST",
        "register extract ETTEST database share automatic",
        "add extract ETTEST, integrated tranlog, begin now",
        "add exttrail /u01/app/ogg/dirdat/tt, extract ETTEST, megabytes 100",
        "add replicat RTEST, integrated, exttrail /u01/app/ogg/dirdat/tt, "
        "checkpointtable OGG_BSH.RTEST",
        "delete replicat RTEST",
        "delete extract ETTEST",
        "unregister extract ETTEST database",
    ):
        gg.kiem_pham_vi(script, cp)       # khong duoc raise


@test
def pham_vi_tu_choi_khi_phien_chua_tao_ten_nao():
    try:
        gg.kiem_pham_vi("delete extract ETTEST", set())
    except gg.Blocked as e:
        assert "chưa tạo tên nào" in str(e), str(e)
        return
    raise AssertionError("cho_phep rong ma khong bi tu choi")


@test
def pham_vi_khong_dung_cho_lenh_thuong():
    """Ham nay CHI dung cho nhom lenh pham vi - khong phai cua sau chung."""
    for script in ("info all", "stop replicat RTEST", "stats replicat RTEST"):
        try:
            gg.kiem_pham_vi(script, {"RTEST"})
        except gg.Blocked:
            continue
        raise AssertionError(f"kiem_pham_vi phai tu choi lenh thuong: {script!r}")


@test
def danh_sach_bat_kha_xam_pham_phu_het_doi_tuong_dang_chay():
    for t in ("ETPMNV", "ETHT", "ETDTHU", "RPMNVODS", "RHT", "RDTHU", "MGR",
              "PM", "HT", "DT",
              "OGG_BSH.RPMNVODS", "OGG_BSH.RHT", "OGG_BSH.RDTHU"):
        assert t in gg.BAT_KHA_XAM_PHAM, t
    # va KHONG chua ten thu nghiem
    for t in ("ETTEST", "RTEST", "TT"):
        assert t not in gg.BAT_KHA_XAM_PHAM, t


@test
def blocklist_van_chan_nhom_pham_vi_o_duong_thuong():
    """Ngoai wizard thi nhom lenh do VAN bi chan nhu moi lenh khac."""
    for script in ("register extract ETTEST database share automatic",
                   "add extract ETTEST, integrated tranlog, begin now",
                   "delete replicat RTEST",
                   "unregister extract ETTEST database",
                   "add checkpointtable OGG_BSH.RTEST"):
        try:
            gg.check_script(script)
        except gg.Blocked:
            continue
        raise AssertionError(f"duong thuong phai chan: {script!r}")


# =====================================================================
# LUONG_MOI (2026-10-15) - hai danh sach khac nhau, va goi y ten.
#
# Moi ca o duoi ma doi bien toan cuc deu phai tra lai trong `finally`:
# PROCESSES / BAT_KHA_XAM_PHAM bay gio la `set`, khong con `frozenset`.
# =====================================================================

@test
def dang_ky_process_KHONG_lam_ten_moi_thanh_bat_kha_xam_pham():
    """Neu dua luon vao danh sach bat kha xam pham thi chinh phien tao ra
    no se khong don sach duoc - capture mo coi o lai DB nguon."""
    try:
        gg.dang_ky_process("ETTEST", "RTEST")
        assert "ETTEST" in gg.PROCESSES and "RTEST" in gg.PROCESSES
        assert "ETTEST" not in gg.BAT_KHA_XAM_PHAM
        assert "RTEST" not in gg.BAT_KHA_XAM_PHAM
        # va van xoa duoc trong pham vi phien
        gg.kiem_pham_vi("delete extract ETTEST", {"ETTEST"})
    finally:
        gg.PROCESSES.difference_update({"ETTEST", "RTEST"})


@test
def khoa_dang_chay_lam_ten_do_thanh_bat_kha_xam_pham():
    try:
        gg.khoa_dang_chay({"ETTEST"})
        try:
            gg.kiem_pham_vi("delete extract ETTEST", {"ETTEST"})
        except gg.Blocked as e:
            assert "bất khả xâm phạm" in str(e), str(e)
        else:
            raise AssertionError("da khoa ma van xoa duoc")
    finally:
        gg.BAT_KHA_XAM_PHAM.discard("ETTEST")


@test
def luong_moi_van_khong_cham_duoc_doi_tuong_dang_chay():
    """Lop 3 truoc lop 2: phien LUONG_MOI khai gi cung khong qua duoc."""
    cp = {"ETBC2", "RBC2", "bc", "OGG_BSH.RBC2"}
    for script in ("delete replicat RHT",
                   "unregister extract ETPMNV database",
                   "add exttrail /u01/app/ogg/dirdat/ht, extract ETBC2",
                   "delete checkpointtable OGG_BSH.RDTHU",
                   "delete extract ETDTHU"):
        try:
            gg.kiem_pham_vi(script, cp)
        except gg.Blocked as e:
            assert "bất khả xâm phạm" in str(e), f"{script}: {e}"
            continue
        raise AssertionError(f"phai tu choi: {script!r}")


@test
def ten_extract_phai_bat_dau_bang_ET():
    """start/stop va dblogin suy vai tro tu 2 ky tu dau cua ten."""
    assert gg.kiem_ten_moi("etbc2", "extract") == "ETBC2"
    try:
        gg.kiem_ten_moi("BC2", "extract")
    except gg.Blocked as e:
        assert "bắt đầu bằng ET" in str(e), str(e)
        return
    raise AssertionError("Extract khong bat dau bang ET ma van qua")


@test
def ten_replicat_PHAI_bat_dau_bang_R():
    assert gg.kiem_ten_moi("rbc2", "replicat") == "RBC2"
    for xau in ("ETX", "BC2", "XRBC"):
        try:
            gg.kiem_ten_moi(xau, "replicat")
        except gg.Blocked as e:
            assert "bắt đầu bằng R" in str(e), str(e)
            continue
        raise AssertionError(f"Replicat ten {xau} ma van qua")


# ------------------------------------------------------------- goi y ten
DANG_DUNG = {"ETPMNV", "RPMNVODS", "PM", "ETHT", "RHT", "HT",
             "ETDTHU", "RDTHU", "DT"}


@test
def goi_y_khong_bao_gio_tra_ten_dang_chay():
    g = gg.goi_y_ten("HT", DANG_DUNG)
    assert g["extract"] not in ("ETHT",) and g["replicat"] not in ("RHT",)
    assert g["trail_prefix"] not in ("ht", "pm", "dt")


@test
def moi_ten_goi_y_deu_qua_duoc_kiem_ten_moi():
    for ma in ("BC2", "HT", "PM", "KT", "XYZ"):
        g = gg.goi_y_ten(ma, DANG_DUNG)
        assert gg.kiem_ten_moi(g["extract"], "extract") == g["extract"], ma
        assert gg.kiem_ten_moi(g["replicat"], "replicat") == g["replicat"], ma
        assert gg.kiem_ten_moi(g["trail_prefix"], "trail") == g["trail_prefix"], ma


@test
def tien_to_trail_goi_y_luon_la_DUNG_2_chu_thuong():
    for ma in ("BC2", "HT", "P", "KT_SO_SO", "X1"):
        g = gg.goi_y_ten(ma, DANG_DUNG)
        if g["trail_prefix"]:
            assert gg.TRAIL_PREFIX.match(g["trail_prefix"]), (ma, g)


@test
def goi_y_replicat_khong_bao_gio_bat_dau_bang_ET():
    for ma in ("ET", "ETX", "ETHT"):
        g = gg.goi_y_ten(ma, DANG_DUNG)
        assert not g["replicat"].startswith("ET"), (ma, g)


@test
def goi_y_suy_duoc_ma_tu_ten_bang():
    g = gg.goi_y_ten("", DANG_DUNG, bang="HT_MA_KENH_BAN")
    assert g["stream_id"] == "HT"
    assert g["extract"] and g["extract"] != "ETHT"


@test
def goi_y_la_TAT_DINH():
    a = gg.goi_y_ten("BC2", DANG_DUNG)
    b = gg.goi_y_ten("BC2", DANG_DUNG)
    assert a == b


@test
def goi_y_het_cho_thi_bao_chu_khong_tra_ten_trung():
    """Khoa het khong gian ten hep lai -> phai bao, khong duoc tra bua."""
    kin = set(DANG_DUNG) | {f"ET{'BC'}"} | {f"ETB{i}" for i in range(2, 10)}
    kin |= {f"R{'BC'}"} | {f"RB{i}" for i in range(2, 10)}
    g = gg.goi_y_ten("BC", kin)
    for ten in (g["extract"], g["replicat"]):
        assert ten.upper() not in {x.upper() for x in kin}, g
