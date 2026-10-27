"""Bieu do log: do dung luong MOI loai log phat sinh tren server.

Muc dich: thay ro log dang TANG nhanh den dau va dia con CHUA duoc bao lau.

Chi DOC: stat() tung file, khong mo, khong xoa, khong nen. Don log la viec
cua DBA, cong cu chi chi ra cho nao dang phinh.

NHOM LOG - khao sat that tren 172.16.33.90 ngay 2026-10-26
  trail GG (dirdat)          ~400 MB  - purge tu dong nen rong it, PHAT SINH nhieu
  audit Oracle (adump)       ~220 MB  - moi phien sysdba mot file, khong ai don
  alert/trace/incident        ~60 MB
  so lieu + sao luu ggadmin  ~730 MB  - ggadmin.db, 3 ban .premigrate, 30 ban backup
  listener, ggserr, dirrpt, /var/log: vai MB den vai chuc MB
DB dich chay NOARCHIVELOG -> KHONG co archive log tren may nay. Redo log co
dinh 3 x 200 MB, khong tang -> khong dua vao.

HAI CON SO KHAC NHAU, DUNG NHAM LA DOC SAI
  - TANG RONG = cuoi khoang - dau khoang. Day la con so quyet dinh dia day.
  - PHAT SINH = tong cac lan TANG giua hai lan do lien tiep, bo qua lan
    GIAM (purge trail, xoay vong ggserr.log). Trail purge lien tuc nen tang
    rong gan 0 nhung phat sinh moi ngay co the hang GB - nhin rong khong thay.
  Phat sinh la so XAP XI DUOI: trong 5 phut neu vua ghi vua purge thi phan bu
  tru nhau khong thay duoc.
"""

from __future__ import annotations

import asyncio
import glob
import os
import shutil
import sqlite3
import time
from pathlib import Path

# Do 10 tick mot lan = 5 phut voi poll 30s. Duyet vai nghin file mat duoi
# mot giay, nhung moi lan do ghi ~30 diem; moi tick thi 90 ngay la ~2,5 trieu
# dong chi cho log - khong dang.
DO_MOI_N_TICK = 10
# Nguong chuyen app sang CHI DOC (collector.READ_ONLY_GB). Lap lai o day de
# module nay khong import collector (collector import module nay).
CHI_DOC_GB = 25.0


# Moi nhom co HAN MUC rieng (tran_gb trong nhom_log). Dung qua NGUONG_PCT %
# han muc -> to DO, duoi -> XANH. Ca han muc lan nguong sua duoc trong
# conf/app.toml, khong can sua code:
#   [log_tran]
#   nguong_pct = 50
#   gg_trail   = 5      # GB, theo khoa cua nhom
NGUONG_PCT = 50.0


def han_muc(cfg) -> tuple[dict[str, float], float]:
    """({key: han muc byte}, nguong %) sau khi ap ghi de tu app.toml."""
    over = dict(getattr(cfg, "log_tran", None) or {})
    nguong = float(over.pop("nguong_pct", NGUONG_PCT))
    tran = {}
    for g in nhom_log(cfg):
        gb = over.get(g["key"], g["tran_gb"])
        tran[g["key"]] = float(gb) * 1024**3
    return tran, nguong


def _oracle_base(cfg) -> Path:
    # /u01/app/oracle/product/19.3.0.0/dbhome_1 -> /u01/app/oracle
    return cfg.oracle_home.parent.parent.parent


def nhom_log(cfg) -> list[dict]:
    """Danh sach nhom log. Moi `duong` la mot glob: khop FILE thi tinh file,
    khop THU MUC thi duyet de quy ca thu muc.

    Them loai log moi = them mot dong o day. Glob khong khop gi thi nhom do
    = 0 byte, khong loi.
    """
    gg = cfg.gg_home
    ob = _oracle_base(cfg)
    oh = cfg.oracle_home
    app_dir = cfg.data_dir.parent
    return [
        {"key": "gg_trail", "tran_gb": 5, "ten": "Trail GoldenGate", "loai": "GoldenGate",
         "duong": [f"{gg}/dirdat"]},
        {"key": "gg_rpt", "tran_gb": 0.2, "ten": "Report + discard GG", "loai": "GoldenGate",
         "duong": [f"{gg}/dirrpt"]},
        {"key": "gg_err", "tran_gb": 0.05, "ten": "ggserr.log", "loai": "GoldenGate",
         "duong": [f"{gg}/ggserr.log*"]},
        {"key": "ora_audit", "tran_gb": 1, "ten": "Audit Oracle (adump)", "loai": "Oracle",
         "duong": [f"{ob}/admin/*/adump", f"{ob}/audit", f"{oh}/rdbms/audit"]},
        {"key": "ora_alert", "tran_gb": 0.2, "ten": "Alert log DB", "loai": "Oracle",
         "duong": [f"{ob}/diag/rdbms/*/*/alert"]},
        {"key": "ora_trace", "tran_gb": 1, "ten": "Trace + incident DB", "loai": "Oracle",
         "duong": [f"{ob}/diag/rdbms/*/*/trace", f"{ob}/diag/rdbms/*/*/incident",
                   f"{ob}/diag/rdbms/*/*/cdump"]},
        {"key": "ora_lsnr", "tran_gb": 0.2, "ten": "Listener", "loai": "Oracle",
         "duong": [f"{ob}/diag/tnslsnr", f"{oh}/network/log"]},
        {"key": "ora_client", "tran_gb": 0.1, "ten": "Diag client", "loai": "Oracle",
         "duong": [f"{ob}/diag/clients"]},
        {"key": "app_log", "tran_gb": 0.2, "ten": "Log ggadmin", "loai": "ggadmin",
         "duong": [str(cfg.log_dir)]},
        {"key": "app_data", "tran_gb": 3, "ten": "Số liệu + sao lưu ggadmin", "loai": "ggadmin",
         "duong": [str(cfg.data_dir), str(app_dir / "backup")]},
        {"key": "os_log", "tran_gb": 1, "ten": "/var/log (phần đọc được)", "loai": "Hệ điều hành",
         "duong": ["/var/log"]},
    ]


# Xoa duoc khong - chi la HUONG DAN hien tren trang, cong cu KHONG tu xoa.
#   duoc      : log thuan chan doan, xoa khong anh huong he thong
#   dieu_kien : co phan xoa duoc, co phan DANG DUNG - phai chon file
#   khong     : xoa tay la hong he thong / mat du lieu, hoac khong thuoc quyen
XOA = {
    "gg_trail": ("khong",
                 "Trail Replicat có thể chưa đọc xong - xoá tay là mất dữ liệu, Replicat abend. "
                 "Manager đã tự dọn (mgr.prm: PURGEOLDEXTRACTS ... USECHECKPOINTS, "
                 "MINKEEPHOURS 12)."),
    # BAY TEN (2026-10-26): ETPMNV2.rpt vua la ban cu so 2 cua ETPMNV, vua la
    # report HIEN HANH cua nhom ETPMNV2. Khong duoc don theo kieu "file co so
    # o cuoi" - phai doi chieu ten nhom trong `info all`.
    "gg_rpt": ("dieu_kien",
               "Xoá được bản cũ GG tự xoay vòng (<NHÓM>0..9.rpt/.dsc). Giữ <NHÓM>.rpt và <NHÓM>.dsc "
               "hiện hành - .dsc của Replicat là chốt bắt bản ghi bị bỏ. Coi chừng trùng tên: "
               "ETPMNV2.rpt là file HIỆN HÀNH của nhóm ETPMNV2, không phải bản cũ của ETPMNV - "
               "đối chiếu tên nhóm trong info all trước khi xoá."),
    "gg_err": ("dieu_kien",
               "Xoá được bản xoay vòng ggserr.log.1. Không xoá ggserr.log đang ghi - công cụ đếm "
               "ABEND từ file này."),
    "ora_audit": ("duoc",
                  "File .aud của OS audit, xoá không ảnh hưởng DB. Nên giữ theo chính sách kiểm "
                  "toán (vd 90 ngày): find adump -name '*.aud' -mtime +90 -delete."),
    "ora_alert": ("duoc",
                  "Log chẩn đoán. Dọn bằng adrci (purge -age ... -type alert), không rm tay - giữ "
                  "lại phần gần đây để tra lỗi."),
    "ora_trace": ("duoc",
                  "Trace/incident chỉ để chẩn đoán, ADR tự purge (SHORTP_POLICY 720 giờ = 30 ngày). "
                  "Dọn sớm bằng "
                  "adrci purge; xoá file tiến trình nền đang mở không giải phóng chỗ."),
    "ora_lsnr": ("duoc",
                 "Xoá được. listener.log đang mở - cắt rỗng (: > listener.log), không rm."),
    "ora_client": ("duoc", "Trace phía client, xoá được."),
    "app_log": ("dieu_kien",
                "Xoá được job_*.log, *.out cũ. Giữ audit-*.jsonl (vết thao tác) và app.log "
                "đang ghi."),
    "app_data": ("dieu_kien",
                 "Không xoá ggadmin.db đang dùng. Xoá được ggadmin.db.*.premigrate cũ; thư mục "
                 "backup tự xoay vòng 30 bản."),
    "os_log": ("khong",
               "Thuộc root, logrotate của hệ điều hành tự quản - user oracle không có quyền xoá."),
}


# ------------------------------------------------------------------- do
def _diem_gan(path: str) -> str:
    """Diem gan (mount point) chua path - de biet nhom log an vao dia nao."""
    p = os.path.realpath(path)
    while not os.path.ismount(p):
        cha = os.path.dirname(p)
        if cha == p:
            break
        p = cha
    return p


def do_nhom(duong: list[str]) -> dict:
    """Tong byte + so file cua mot nhom. Khong dem trung (hard link, glob
    chong nhau). Thu muc khong doc duoc thi bo qua, khong no."""
    da_thay: set[tuple[int, int]] = set()
    tong = 0
    so_file = 0
    goc = ""

    def _cong(f: str) -> None:
        nonlocal tong, so_file
        try:
            st = os.lstat(f)
        except OSError:
            return
        if not os.path.isfile(f) or os.path.islink(f):
            return
        k = (st.st_dev, st.st_ino)
        if k in da_thay:
            return
        da_thay.add(k)
        tong += st.st_size
        so_file += 1

    for mau in duong:
        for p in sorted(glob.glob(mau)):
            goc = goc or p
            if os.path.isdir(p) and not os.path.islink(p):
                for thu_muc, _, files in os.walk(p, onerror=lambda e: None):
                    for f in files:
                        _cong(os.path.join(thu_muc, f))
            else:
                _cong(p)
    return {"bytes": tong, "files": so_file,
            "mount": _diem_gan(goc) if goc else "/"}


def do(cfg) -> dict:
    """{ts, nhom: {key: {bytes, files, mount}}, dia: {mount: {total, used, avail}}}"""
    nhom = {g["key"]: do_nhom(g["duong"]) for g in nhom_log(cfg)}
    dia: dict[str, dict] = {}
    for m in {v["mount"] for v in nhom.values()} | {"/"}:
        try:
            t, u, f = shutil.disk_usage(m)
            dia[m] = {"total": float(t), "used": float(u), "avail": float(f)}
        except OSError:
            pass
    return {"ts": int(time.time()), "nhom": nhom, "dia": dia}


async def a_do(cfg) -> dict:
    return await asyncio.to_thread(do, cfg)


def hang_luu(m: dict) -> list[tuple]:
    """Dong de ghi vao metric_sample."""
    ts = m["ts"]
    rows: list[tuple] = []
    for k, v in m["nhom"].items():
        rows.append(("LOG", k, "bytes", ts, float(v["bytes"])))
        rows.append(("LOG", k, "files", ts, float(v["files"])))
    for mnt, d in m["dia"].items():
        rows.append(("LOGFS", mnt, "total_bytes", ts, d["total"]))
        rows.append(("LOGFS", mnt, "used_bytes", ts, d["used"]))
    return rows


# ------------------------------------------------------------- doc lai
def _chuoi(con: sqlite3.Connection, scope: str, key: str, metric: str,
           since: int, dung_gio: bool) -> list[tuple[int, float]]:
    """Chuoi (ts, val) tang dan. Khoang dai dung ban gom theo gio (v_last =
    gia tri CUOI gio, dung cho dung luong) roi noi them so lieu goc cua gio
    chua gom de diem cuoi la diem moi nhat."""
    if not dung_gio:
        return [(r[0], r[1]) for r in con.execute(
            "SELECT ts, val FROM metric_sample WHERE scope=? AND scope_key=?"
            " AND metric=? AND ts>=? ORDER BY ts", (scope, key, metric, since))]
    out = [(r[0], r[1]) for r in con.execute(
        "SELECT hour_ts, v_last FROM metric_hourly WHERE scope=? AND scope_key=?"
        " AND metric=? AND hour_ts>=? ORDER BY hour_ts", (scope, key, metric, since))]
    moc = out[-1][0] + 3600 if out else since
    out += [(r[0], r[1]) for r in con.execute(
        "SELECT ts, val FROM metric_sample WHERE scope=? AND scope_key=?"
        " AND metric=? AND ts>=? ORDER BY ts", (scope, key, metric, moc))]
    return out


def tang_truong(diem: list[tuple[int, float]]) -> dict:
    """Tang rong, phat sinh, va toc do theo ngay cua mot chuoi dung luong."""
    if len(diem) < 2:
        return {"rong": None, "phat_sinh": None, "rong_ngay": None,
                "phat_sinh_ngay": None, "so_ngay": 0.0}
    rong = diem[-1][1] - diem[0][1]
    phat_sinh = sum(max(0.0, b[1] - a[1]) for a, b in zip(diem, diem[1:]))
    so_ngay = (diem[-1][0] - diem[0][0]) / 86400
    # Duoi 1 gio thi chia ra "theo ngay" chi la nhan nhieu len - khong tin.
    du = so_ngay >= 1 / 24
    return {"rong": rong, "phat_sinh": phat_sinh, "so_ngay": so_ngay,
            "rong_ngay": rong / so_ngay if du else None,
            "phat_sinh_ngay": phat_sinh / so_ngay if du else None}


def do_doc(diem: list[tuple[int, float]]) -> float | None:
    """Do doc (byte/ngay) theo binh phuong toi thieu. Dung cho dung luong
    da dung cua dia: mot lan don dep le khong lam lech het nhu cuoi-tru-dau."""
    n = len(diem)
    if n < 2 or diem[-1][0] - diem[0][0] < 3600:
        return None
    x0 = diem[0][0]
    xs = [(t - x0) / 86400 for t, _ in diem]
    ys = [v for _, v in diem]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def tang_theo_ngay(diem: list[tuple[int, float]]) -> list[tuple[str, float]]:
    """Muc tang cua TUNG NGAY (gio dia phuong): gia tri cuoi ngay tru gia tri
    cuoi ngay lien truoc. Ngay dau khoang khong co ngay truoc nen bo."""
    cuoi: dict[str, float] = {}
    for t, v in diem:
        cuoi[time.strftime("%Y-%m-%d", time.localtime(t))] = v
    ngay = sorted(cuoi)
    return [(b, cuoi[b] - cuoi[a]) for a, b in zip(ngay, ngay[1:])]


def toc_do_thuong(diem: list[tuple[int, float]]) -> tuple[float | None, list]:
    """(toc do THUONG NGAY byte/ngay, cac ngay DOT BIEN).

    VI SAO KHONG CHI DUNG do_doc() - gap that 2026-10-26: dia / tang 49 GB
    trong ngay 23/10 vi nap du lieu PMNV3 (datafile), ngay thuong chi
    0,1-0,3 GB. Duong thang qua 7 ngay cho 10,7 GB/ngay -> "21 ngay toi
    nguong chi doc" - bao dong sai. Trung vi muc tang tung ngay khong bi mot
    ngay dot bien keo lech. Can it nhat 3 ngay tron, khong thi dung do_doc().
    """
    ds = tang_theo_ngay(diem)
    if len(ds) < 3:
        return do_doc(diem), []
    vals = sorted(v for _, v in ds)
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    # Dot bien: tren 1 GiB VA gap 5 lan muc thuong ngay.
    dot = [(d, v) for d, v in ds if v > max(1024**3, 5 * abs(med))]
    return med, dot


def _so_ngay_con(con_trong: float, toc_do: float | None) -> float | None:
    """Con bao nhieu ngay thi het `con_trong` byte. None = khong tang."""
    if toc_do is None or toc_do <= 0:
        return None
    return max(0.0, con_trong) / toc_do


def bao_cao(con: sqlite3.Connection, cfg, hours: int, moi_nhat: dict | None,
            kem_diem: bool = False) -> dict:
    """Tong hop cho trang Bieu do log.

    `moi_nhat` la lan do gan nhat trong bo nho (collector); None thi lay
    diem cuoi trong DB.
    """
    now = int(time.time())
    since = now - hours * 3600
    dung_gio = hours > 72
    nhom_cfg = nhom_log(cfg)
    tran_map, nguong_pct = han_muc(cfg)
    moi = (moi_nhat or {}).get("nhom", {})

    nhom = []
    for g in nhom_cfg:
        diem = _chuoi(con, "LOG", g["key"], "bytes", since, dung_gio)
        hien = moi.get(g["key"])
        if hien is None and diem:
            hien = {"bytes": diem[-1][1], "files": None, "mount": "/"}
            r = con.execute("SELECT val FROM metric_sample WHERE scope='LOG'"
                            " AND scope_key=? AND metric='files' ORDER BY ts DESC"
                            " LIMIT 1", (g["key"],)).fetchone()
            hien["files"] = r[0] if r else None
        hien = hien or {"bytes": 0, "files": 0, "mount": "/"}
        tran = tran_map[g["key"]]
        pct = (hien["bytes"] or 0) / tran * 100 if tran > 0 else 0.0
        row = {**{k: g[k] for k in ("key", "ten", "loai", "duong")},
               "bytes": hien["bytes"], "files": hien["files"],
               "mount": hien["mount"], **tang_truong(diem),
               "tran": tran, "pct_tran": pct, "vuot": pct > nguong_pct,
               "xoa": XOA.get(g["key"], ("khong", ""))[0],
               "xoa_ghi_chu": XOA.get(g["key"], ("khong", ""))[1]}
        if kem_diem:
            row["diem"] = [[t * 1000, v] for t, v in diem]
        nhom.append(row)

    tong = sum(n["bytes"] or 0 for n in nhom)
    for n in nhom:
        n["ty_le"] = (n["bytes"] or 0) / tong * 100 if tong else 0.0

    def _cong(k: str) -> float | None:
        vals = [n[k] for n in nhom if n[k] is not None]
        return sum(vals) if vals else None

    # ---- kha nang chua cua tung dia ----
    dia_moi = (moi_nhat or {}).get("dia", {})
    if not dia_moi:
        try:
            t, u, f = shutil.disk_usage("/")
            dia_moi = {"/": {"total": float(t), "used": float(u), "avail": float(f)}}
        except OSError:
            dia_moi = {}
    dia = []
    for mnt, d in sorted(dia_moi.items()):
        # "/" co san FS/used_bytes do moi 30s tu ngay dau -> du bao duoc
        # ngay, khong phai doi Bieu do log tich du lieu.
        if mnt == "/":
            diem = _chuoi(con, "FS", "/", "used_bytes", since, dung_gio)
        else:
            diem = _chuoi(con, "LOGFS", mnt, "used_bytes", since, dung_gio)
        doc = do_doc(diem)
        thuong, dot_bien = toc_do_thuong(diem)
        log_rong_ngay = sum(n["rong_ngay"] or 0 for n in nhom if n["mount"] == mnt)
        nguong = CHI_DOC_GB * 1024**3 if mnt == "/" else 0.0
        row = {
            "mount": mnt, "total": d["total"], "used": d["used"], "avail": d["avail"],
            "used_pct": d["used"] / d["total"] * 100 if d["total"] else 0.0,
            "log_bytes": sum(n["bytes"] or 0 for n in nhom if n["mount"] == mnt),
            "doc_ngay": doc,                 # trung binh ca khoang (duong thang)
            "thuong_ngay": thuong,           # trung vi tung ngay - dung de du bao
            "dot_bien": dot_bien,
            "nguong_chi_doc": nguong,
            "ngay_den_day": _so_ngay_con(d["avail"], thuong),
            "ngay_den_chi_doc": _so_ngay_con(d["avail"] - nguong, thuong) if nguong else None,
            "ngay_den_chi_doc_tb": _so_ngay_con(d["avail"] - nguong, doc) if nguong else None,
            "ngay_den_day_chi_log": _so_ngay_con(d["avail"], log_rong_ngay or None),
        }
        if kem_diem:
            row["diem"] = [[t * 1000, v] for t, v in diem]
        dia.append(row)
    dia_map = {d["mount"]: d for d in dia}
    for n in nhom:
        d = dia_map.get(n["mount"])
        n["ngay_lam_day"] = _so_ngay_con(d["avail"], n["rong_ngay"]) if d else None

    return {
        "tu": since * 1000, "den": now * 1000, "hours": hours,
        "nguon": ("bản gom theo giờ (giá trị cuối giờ)" if dung_gio
                  else "số liệu gốc, 5 phút một điểm"),
        "do_luc": (moi_nhat or {}).get("ts"),
        "nguong_pct": nguong_pct,
        "so_vuot": sum(1 for n in nhom if n["vuot"]),
        "nhom": nhom,
        "tong": {"bytes": tong, "rong": _cong("rong"), "phat_sinh": _cong("phat_sinh"),
                 "rong_ngay": _cong("rong_ngay"),
                 "phat_sinh_ngay": _cong("phat_sinh_ngay"),
                 "files": sum(n["files"] or 0 for n in nhom)},
        "dia": dia,
    }
