"""Test guard param file + cac helper sua co cau truc.

Moi ca o day la mot loi DA XAY RA THAT trong du an, hoac mot loi ma kien truc
he nay bao dam se xay ra neu khong chan.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import params as P                                     # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# Mau param file Replicat that, da luoc gon
RHT = """\
REPLICAT RHT
USERIDALIAS bshc_ogg
ASSUMETARGETDEFS
DISCARDFILE ./dirrpt/RHT.dsc, APPEND, MEGABYTES 100
DBOPTIONS INTEGRATEDPARAMS(max_sga_size 256)
REPERROR (0001, DISCARD)
REPERROR (1403, DISCARD)
DDL INCLUDE MAPPED
MAP KT_BSH.HT_MA_NSD, TARGET ODS.HT_MA_NSD, COLMAP (USEDEFAULTS);
MAP KT_BSH.HT_NSD_PVI, TARGET ODS.HT_NSD_PVI, KEYCOLS (NGUON, MA_DVI, MA, MA_NHOM, DVI, PHONG), COLMAP (USEDEFAULTS);
MAP KT_BSH.TBH_PBO, TARGET ODS.TBH_PBO, KEYCOLS (MA_DVI, SO_ID), HANDLECOLLISIONS, COLMAP (USEDEFAULTS);
"""

ETHT = """\
EXTRACT ETHT
USERIDALIAS pmnv_ogg
EXTTRAIL ./dirdat/ht
TRANLOGOPTIONS INTEGRATEDPARAMS (max_sga_size 256)
DDL INCLUDE MAPPED
TABLE KT_BSH.HT_MA_NSD;
TABLE KT_BSH.HT_NSD_PVI;
TABLE KT_BSH.TBH_PBO;
"""


def _guard(old, new, process="RHT"):
    return P.guard(old, new, process=process)


# =========================================================== mat khau dang ro
@test
def chan_dua_lai_mat_khau_dang_ro():
    """Phase 0 da dua he nay ve 0/7 file co mat khau ro. Khong de thoai lui."""
    xau = RHT.replace("USERIDALIAS bshc_ogg",
                      "USERID OGG_BSH, PASSWORD BSHabc123")
    loi = _guard(RHT, xau)
    # Kiem tu khoa KY THUAT (`USERID`, `PASSWORD`) thay vi nguyen van
    # thong diep: thong diep la van ban cho nguoi doc va se con doi.
    assert any("USERID" in l and "PASSWORD" in l for l in loi), loi


@test
def chan_bo_useridalias():
    xau = RHT.replace("USERIDALIAS bshc_ogg\n", "")
    loi = _guard(RHT, xau)
    assert any("USERIDALIAS" in l and "không có" in l for l in loi), loi


# ================================================================== MEGABYTES
@test
def chan_MEGABYTES_tren_tham_so_EXTTRAIL():
    """Da gap that: checkprm bao
    `parameter [exttrail] has unrecognized keyword or extra value "MEGABYTES"`.
    MEGABYTES chi hop le tren LENH `ADD EXTTRAIL`, khong phai tham so trong .prm.
    """
    xau = ETHT.replace("EXTTRAIL ./dirdat/ht",
                       "EXTTRAIL ./dirdat/ht, MEGABYTES 100")
    loi = _guard(ETHT, xau, process="ETHT")
    assert any("MEGABYTES" in l for l in loi), loi


@test
def MEGABYTES_tren_DISCARDFILE_thi_hop_le():
    """`DISCARDFILE ... MEGABYTES 100` la dung - khong duoc bao dong gia."""
    xau = RHT.replace("REPERROR (0001, DISCARD)\n", "")
    loi = _guard(RHT, xau)
    assert not any("MEGABYTES" in l for l in loi), loi


# ========================================================== HANDLECOLLISIONS
@test
def chan_HANDLECOLLISIONS_toan_cuc():
    """Bat toan cuc thi no am tham bo qua loi tren MOI bang, ke ca bang dang
    chay on dinh. Phai dat tren tung dong MAP dang load."""
    xau = RHT.replace("DDL INCLUDE MAPPED", "HANDLECOLLISIONS\nDDL INCLUDE MAPPED")
    loi = _guard(RHT, xau)
    assert any("HANDLECOLLISIONS" in l and "toàn cục" in l for l in loi), loi


@test
def HANDLECOLLISIONS_tren_dong_MAP_thi_hop_le():
    xau = RHT.replace(
        "MAP KT_BSH.HT_MA_NSD, TARGET ODS.HT_MA_NSD,",
        "MAP KT_BSH.HT_MA_NSD, TARGET ODS.HT_MA_NSD, HANDLECOLLISIONS,")
    assert _guard(RHT, xau) == [], _guard(RHT, xau)


# ============================================================ giam MAP/TABLE
@test
def chan_giam_so_dong_MAP():
    """Bo MAP trong khi dong TABLE con lai = MAT DU LIEU IM LANG.

    Extract van dao redo va ghi trail, Replicat bo qua ban ghi VA VAN DOI
    CHECKPOINT: khong discard, khong log, info all van RUNNING lag 0.
    """
    xau = "\n".join(l for l in RHT.splitlines()
                    if "HT_NSD_PVI" not in l) + "\n"
    loi = _guard(RHT, xau)
    assert any("MẤT DỮ LIỆU IM LẶNG" in l for l in loi), loi
    assert any("3 -> 2" in l for l in loi), loi


@test
def chan_giam_so_dong_TABLE_cua_Extract():
    xau = "\n".join(l for l in ETHT.splitlines()
                    if "TBH_PBO" not in l) + "\n"
    loi = _guard(ETHT, xau, process="ETHT")
    assert any("TABLE giảm" in l for l in loi), loi
    assert any("Gỡ bảng khỏi luồng" in l for l in loi), loi


@test
def them_MAP_thi_duoc_qua():
    xau = RHT + ("MAP KT_BSH.TT_1, TARGET ODS.TT_1, "
                 "KEYCOLS (MA_DVI, SO_ID), COLMAP (USEDEFAULTS);\n")
    assert _guard(RHT, xau) == []


@test
def file_rong_bi_chan():
    assert any("rong" in l for l in _guard(RHT, "   \n"))


@test
def file_qua_lon_bi_chan():
    assert any("quá lớn" in l for l in _guard(RHT, "X\n" * 400_000))


# ===================================================================== thong ke
@test
def diff_stats_dem_dung():
    xau = RHT + ("MAP KT_BSH.TT_1, TARGET ODS.TT_1, COLMAP (USEDEFAULTS);\n")
    st = P.diff_stats(RHT, xau)
    assert st["map_truoc"] == 3 and st["map_sau"] == 4, st
    assert st["them"] == 1 and st["xoa"] == 0, st
    st2 = P.diff_stats(ETHT, ETHT)
    assert st2["table_truoc"] == st2["table_sau"] == 3, st2


@test
def diff_co_dong_them_va_bot():
    xau = RHT.replace("max_sga_size 256", "max_sga_size 512")
    d = P.diff(RHT, xau, name="rht.prm")
    assert "-DBOPTIONS INTEGRATEDPARAMS(max_sga_size 256)" in d, d
    assert "+DBOPTIONS INTEGRATEDPARAMS(max_sga_size 512)" in d, d


# ============================================================ helper cau truc
@test
def them_dong_table_idempotent():
    a = P.them_dong_table(ETHT, "KT_BSH", "TT_1")
    assert "TABLE KT_BSH.TT_1;" in a
    b = P.them_dong_table(a, "KT_BSH", "TT_1")
    assert a == b, "goi lan hai khong duoc them trung"
    # bang da co thi khong them
    assert P.them_dong_table(ETHT, "KT_BSH", "TBH_PBO") == ETHT


@test
def them_dong_map_day_du():
    out = P.them_dong_map(RHT, "KT_BSH", "TT_1", "ODS", "TT_1",
                          "MA_DVI, SO_ID", True)
    dong = out.splitlines()[-1]
    assert dong.startswith("MAP KT_BSH.TT_1, TARGET ODS.TT_1,")
    assert "KEYCOLS (MA_DVI, SO_ID)" in dong
    assert "HANDLECOLLISIONS" in dong
    assert dong.endswith("COLMAP (USEDEFAULTS);")
    # idempotent
    assert P.them_dong_map(out, "KT_BSH", "TT_1", "ODS", "TT_1",
                           "MA_DVI, SO_ID", True) == out


@test
def them_dong_map_khong_keycols_thi_khong_in_keycols():
    out = P.them_dong_map(RHT, "KT_BSH", "TT_9", "ODS", "TT_9", None, False)
    dong = out.splitlines()[-1]
    assert "KEYCOLS" not in dong and "HANDLECOLLISIONS" not in dong, dong


@test
def bo_handlecollisions_dung_mot_bang():
    out = P.bo_handlecollisions(RHT, "KT_BSH", "TBH_PBO")
    assert "HANDLECOLLISIONS" not in out, out
    # cac dong MAP khac khong bi dong vao
    assert out.count("MAP KT_BSH.") == 3
    assert "KEYCOLS (MA_DVI, SO_ID)" in out
    # bang khong co HANDLECOLLISIONS thi khong doi gi
    assert P.bo_handlecollisions(out, "KT_BSH", "HT_MA_NSD") == out


@test
def bo_handlecollisions_khong_dong_vao_bang_ten_gan_giong():
    """`TBH_PBO` va `TBH_PBO_CT` la hai bang khac nhau."""
    src = RHT + ("MAP KT_BSH.TBH_PBO_CT, TARGET ODS.TBH_PBO_CT, "
                 "HANDLECOLLISIONS, COLMAP (USEDEFAULTS);\n")
    out = P.bo_handlecollisions(src, "KT_BSH", "TBH_PBO")
    assert "TBH_PBO_CT, TARGET ODS.TBH_PBO_CT, HANDLECOLLISIONS" in out, \
        "chi duoc bo cua dung bang da chi dinh"


# =====================================================================
# HOI QUY: discard file - dem DONG, khong dem BYTE.
#
# Loi da gap that 2026-10-14: so `os.path.getsize()` voi mot "moc rong"
# (118/113/115 byte). Khi Replicat STOP, GG ghi them mot dong banner
# `process stopped, group RHT : <thoi diem>` -> file to len 94 byte MA
# KHONG CO BAN GHI NAO BI BO. Hau qua: canh bao no sai moi lan bao tri,
# va pre-flight LOAD da chan OAN dung buoc NAP cua wizard ADD_TABLE.
# =====================================================================

@test
def discard_file_rong_chi_co_banner_thi_khong_bao_dong():
    import tempfile
    from pathlib import Path
    from app.adapters import osfs

    class FakeCfg:
        def __init__(self, root):
            self.root = Path(root)
            self.discard_baseline = {"RHT": 113}

        def discard_path(self, rep):
            return self.root / f"{rep}.dsc"

    # Noi dung THAT cua RHT.dsc sau mot lan stop - 207 byte, 0 ban ghi bi bo
    noi_dung = (
        "Oracle GoldenGate Delivery for Oracle process started, group RHT "
        "discard file opened: 2026-10-14 08:17:35.664791\n"
        "Oracle GoldenGate Delivery for Oracle process stopped, group RHT : "
        "2026-10-14 10:17:01.302142\n")
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        with (Path(td) / "RHT.dsc").open("w", encoding="utf-8",
                                         newline="") as fh:
            fh.write(noi_dung)
        d = osfs.discard_info(cfg, "RHT")
        assert d["so_dong_la"] == 0, d
        # kich thuoc VUOT moc rong - day chinh la cho ma cach cu bao dong sai
        assert d["delta_bytes"] > 0, d
        assert d["bytes"] == len(noi_dung), d


@test
def discard_file_co_ban_ghi_bi_bo_thi_PHAI_bao_dong():
    import tempfile
    from pathlib import Path
    from app.adapters import osfs

    class FakeCfg:
        def __init__(self, root):
            self.root = Path(root)
            self.discard_baseline = {"RHT": 113}

        def discard_path(self, rep):
            return self.root / f"{rep}.dsc"

    noi_dung = (
        "Oracle GoldenGate Delivery for Oracle process started, group RHT "
        "discard file opened: 2026-10-14 08:17:35\n"
        "Current time: 2026-10-14 10:30:00\n"
        "Discarded record from action ABEND on error 1403\n"
        "No data found. Mapping problem with compressed update record\n"
        "Oracle GoldenGate Delivery for Oracle process stopped, group RHT : x\n")
    with tempfile.TemporaryDirectory() as td:
        cfg = FakeCfg(td)
        with (Path(td) / "RHT.dsc").open("w", encoding="utf-8",
                                         newline="") as fh:
            fh.write(noi_dung)
        d = osfs.discard_info(cfg, "RHT")
        assert d["so_dong_la"] == 3, d


@test
def banner_cua_ca_ba_replicat_deu_duoc_nhan_ra():
    from app.adapters.osfs import RE_BANNER
    for rep in ("RHT", "RPMNVODS", "RDTHU"):
        for kind in ("started", "stopped"):
            l = (f"Oracle GoldenGate Delivery for Oracle process {kind}, "
                 f"group {rep} : 2026-10-14 10:17:01")
            assert RE_BANNER.search(l), l
    # Dong ban ghi bi bo KHONG duoc coi la banner
    for l in ("Discarded record from action ABEND on error 1403",
              "ORA-01403: no data found",
              "Aborting transaction on ./dirdat/ht beginning at seqno 7"):
        assert not RE_BANNER.search(l), l
