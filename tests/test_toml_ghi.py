"""Test ghi them cau hinh vao app.toml.

Day la module DUY NHAT cua cong cu ghi vao file cau hinh cua mot he dang
chay, nen bo test nay kiem dung mot thu: ba luong dang chay KHONG bi cham.
Thuan tempfile, khong can Oracle, khong can DB.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import toml_ghi as tg                                # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


MAU = '''# ggadmin - cau hinh khong bi mat
[server]
host = "127.0.0.1"
port = 8099
poll_seconds = 30

[paths]
gg_home     = "/u01/app/ogg"
data_dir    = "/u01/app/ggadmin/data"
log_dir     = "/u01/app/ggadmin/logs"
scripts_dir = "/u01/app/ggadmin/scripts"

[oracle]
oracle_home = "/u01/app/oracle/product/19.3.0.0/dbhome_1"
oracle_sid  = "BSHC"

[retention]
raw_days = 90

# Moc RONG cua tung discard file - khac nhau theo DO DAI TEN FILE.
[discard_baseline]
RPMNVODS = 118
RHT      = 113
RDTHU    = 115

# 3 luong doc lap. extract -> trail -> replicat
[streams.PM]
extract  = "ETPMNV"
replicat = "RPMNVODS"
trail    = "pm"
ten      = "Chinh (BH_* 44 + KT_* 8)"

[streams.HT]
extract  = "ETHT"
replicat = "RHT"
trail    = "ht"
ten      = "He thong"

[streams.DT]
extract  = "ETDTHU"
replicat = "RDTHU"
trail    = "dt"
ten      = "Doi thu"

[notify]
enabled = true

[notify.zalo_bot]
chat_ids = ["-123456"]
'''


class _Cfg:
    """Cau hinh toi thieu: dang_ky_luong chi dung app_toml + streams."""

    def __init__(self, path: Path, streams: dict):
        self.app_toml = path
        self.streams = streams


def _file(noi_dung: str = MAU):
    d = tempfile.mkdtemp()
    p = Path(d) / "app.toml"
    p.write_text(noi_dung, encoding="utf-8", newline="\n")
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _luong_cu():
    return {"PM": {"extract": "ETPMNV", "replicat": "RPMNVODS", "trail": "pm"},
            "HT": {"extract": "ETHT", "replicat": "RHT", "trail": "ht"},
            "DT": {"extract": "ETDTHU", "replicat": "RDTHU", "trail": "dt"}}


# ------------------------------------------------------- chi THEM van ban
@test
def noi_dung_cu_la_TIEN_TO_cua_file_moi():
    """Chung minh chi-them: khong mot byte nao cua phan cu bi doi."""
    p = _file()
    goc = p.read_bytes()
    tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc", "Bao cao 2")
    assert p.read_bytes().startswith(goc), "phan cu da bi sua"


@test
def ban_luu_duoc_tao_va_giu_nguyen_ban_cu():
    p = _file()
    goc = p.read_bytes()
    r = tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    assert r["backup"], r
    assert Path(r["backup"]).read_bytes() == goc


@test
def ba_luong_cu_y_nguyen_tung_gia_tri():
    p = _file()
    truoc = tomllib.loads(p.read_text(encoding="utf-8"))
    tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    sau = tomllib.loads(p.read_text(encoding="utf-8"))
    for k in ("PM", "HT", "DT"):
        assert sau["streams"][k] == truoc["streams"][k], k
    for khoi in ("server", "paths", "oracle", "retention",
                 "discard_baseline", "notify"):
        assert sau[khoi] == truoc[khoi], khoi
    assert sau["streams"]["BC2"]["extract"] == "ETBC2"


@test
def chu_thich_van_han_khong_mat_dong_nao():
    """Serialise lai thi mat sach chu thich van hanh - day la chot do."""
    p = _file()
    dem = lambda s: len([d for d in s.splitlines() if d.lstrip().startswith("#")])
    truoc = dem(p.read_text(encoding="utf-8"))
    tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    assert dem(p.read_text(encoding="utf-8")) == truoc


# ------------------------------------------------------------- chay lai
@test
def chay_lai_voi_cung_gia_tri_la_idempotent():
    """App chet giua ghi va cap nhat DB -> lan chay tiep phai khong hong."""
    p = _file()
    tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    sha = _sha(p)
    r = tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    assert r["da_co"] is True and r["backup"] is None
    assert _sha(p) == sha


@test
def cung_ma_luong_khac_gia_tri_thi_TU_CHOI():
    p = _file()
    tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    sha = _sha(p)
    try:
        tg.them_khoi_streams(p, "BC2", "ETBC3", "RBC3", "bd")
    except tg.TomlGhiError:
        assert _sha(p) == sha
        return
    raise AssertionError("ghi de cau hinh luong khac ma khong bi chan")


# --------------------------------------------------------------- trung ten
@test
def trung_extract_replicat_hoac_trail_deu_bi_tu_choi():
    for kw in ({"extract": "ETHT"}, {"replicat": "RHT"}, {"trail": "ht"}):
        p = _file()
        cfg = _Cfg(p, _luong_cu())
        d = {"extract": "ETBC2", "replicat": "RBC2", "trail": "bc"}
        d.update(kw)
        try:
            tg.dang_ky_luong(cfg, "BC2", d["extract"], d["replicat"], d["trail"])
        except tg.TomlGhiError:
            assert "BC2" not in cfg.streams
            continue
        raise AssertionError(f"trung {kw} ma khong bi chan")


# ------------------------------------------------------ hong thi phuc hoi
@test
def khoi_hong_thi_tra_file_ve_BYTE_Y_HET():
    p = _file()
    goc = p.read_bytes()
    that = tg.khoi_streams
    tg.khoi_streams = lambda *a, **k: "\n[streams.XX\nextract = \n"   # TOML vo
    try:
        tg.them_khoi_streams(p, "XX", "ETXX", "RXX", "xx")
    except tg.TomlGhiError:
        pass
    else:
        raise AssertionError("TOML hong ma khong bao loi")
    finally:
        tg.khoi_streams = that
    assert p.read_bytes() == goc, "file khong duoc tra ve ban cu"


@test
def file_khong_ket_thuc_bang_xuong_dong_van_dung():
    p = _file(MAU.rstrip("\n"))
    tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    assert raw["streams"]["BC2"]["extract"] == "ETBC2"
    assert raw["notify"]["zalo_bot"]["chat_ids"] == ["-123456"]


# ------------------------------------------------------- discard baseline
@test
def discard_baseline_chen_DUNG_CHO_khong_roi_vao_bang_cuoi():
    """Append cuoi file thi khoa roi vao [notify.zalo_bot] - bay da thay."""
    p = _file()
    tg.them_discard_baseline(p, "RBC2", 118)
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    assert raw["discard_baseline"]["RBC2"] == 118
    assert "RBC2" not in raw["notify"]["zalo_bot"]
    for k, v in (("RPMNVODS", 118), ("RHT", 113), ("RDTHU", 115)):
        assert raw["discard_baseline"][k] == v


@test
def discard_baseline_chay_lai_khong_ghi_lan_hai():
    p = _file()
    tg.them_discard_baseline(p, "RBC2", 118)
    sha = _sha(p)
    r = tg.them_discard_baseline(p, "RBC2", 999)
    assert r["da_co"] is True and _sha(p) == sha


# ------------------------------------------------------------------ go bo
@test
def go_khoi_tra_file_ve_nhu_cu():
    p = _file()
    goc = p.read_text(encoding="utf-8")
    tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    tg.bo_khoi_streams(p, "BC2")
    assert p.read_text(encoding="utf-8").rstrip("\n") == goc.rstrip("\n")


@test
def go_khoi_khong_lam_mat_luong_dang_chay():
    p = _file()
    tg.them_khoi_streams(p, "BC2", "ETBC2", "RBC2", "bc")
    tg.bo_khoi_streams(p, "BC2")
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    assert set(raw["streams"]) == {"PM", "HT", "DT"}
    assert raw["streams"]["HT"]["replicat"] == "RHT"


@test
def go_khoi_khong_co_thi_khong_lam_gi():
    p = _file()
    sha = _sha(p)
    r = tg.bo_khoi_streams(p, "KHONGCO")
    assert r["da_bo"] is False and _sha(p) == sha


# -------------------------------------------------- nap nong vao bo nho
@test
def dang_ky_luong_nap_nong_cfg_va_PROCESSES():
    from app.adapters import gg

    p = _file()
    cfg = _Cfg(p, _luong_cu())
    try:
        tg.dang_ky_luong(cfg, "BC2", "ETBC2", "RBC2", "bc", "Bao cao 2")
        assert cfg.streams["BC2"]["replicat"] == "RBC2"
        assert "ETBC2" in gg.PROCESSES and "RBC2" in gg.PROCESSES
        # nhung KHONG duoc thanh bat kha xam pham - con phai don sach duoc
        assert "ETBC2" not in gg.BAT_KHA_XAM_PHAM
    finally:
        gg.PROCESSES.difference_update({"ETBC2", "RBC2"})


@test
def dang_ky_that_bai_thi_KHONG_dung_vao_bo_nho():
    p = _file()
    cfg = _Cfg(p, _luong_cu())
    try:
        tg.dang_ky_luong(cfg, "HT2", "ETHT", "RHT2", "h2")   # ETHT da dung
    except tg.TomlGhiError:
        assert "HT2" not in cfg.streams
        assert "HT2" not in tomllib.loads(p.read_text(encoding="utf-8"))["streams"]
        return
    raise AssertionError("trung Extract ma van dang ky duoc")
