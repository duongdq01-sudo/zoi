"""Registry cac bang trong luong + doi chieu 3 nguon su that.

BA NGUON phai khop nhau, lech la co van de:
  1. dong `TABLE KT_BSH.X;`               trong etpmnv/etht/etdthu.prm
  2. dong `MAP KT_BSH.X, TARGET ODS.Y`    trong rpmnvods/rht/rdthu.prm
  3. `dba_capture_prepared_tables@DBL_PMNV`  (da bat TRANDATA o nguon)

CAI BAY SO MOT CUA HE NAY: bang co (1) va (3) nhung THIEU (2).
Extract van dao redo cua bang do va ghi vao trail, nhung Replicat khong co
MAP nen BO QUA ban ghi - va VAN DOI CHECKPOINT. Du lieu mat IM LANG:
discard file khong tang, ggserr.log khong bao gi, `info all` van RUNNING
lag 0. Chi phat hien duoc bang cach doi chieu 3 danh sach nay.

Registry cung la ALLOWLIST lop 5 chong SQL injection (xem adapters/ora.py):
bang ton tai trong ODS nhung ngoai pham vi GG thi cong cu khong duoc cham.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("ggadmin.registry")

# TABLE KT_BSH.BH_HD_GOC;
RE_TABLE = re.compile(r"^TABLE\s+([A-Z0-9_$#]+)\.([A-Z0-9_$#]+)\s*;", re.I)
# MAP KT_BSH.X, TARGET ODS.Y, KEYCOLS (A, B), HANDLECOLLISIONS, COLMAP (...);
RE_MAP = re.compile(
    r"^MAP\s+([A-Z0-9_$#]+)\.([A-Z0-9_$#]+)\s*,\s*TARGET\s+([A-Z0-9_$#]+)\.([A-Z0-9_$#]+)",
    re.I)
RE_KEYCOLS = re.compile(r"KEYCOLS\s*\(([^)]*)\)", re.I)
RE_HC = re.compile(r"\bHANDLECOLLISIONS\b", re.I)


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def parse_extract(path: Path) -> list[tuple[str, str]]:
    """Tra ve [(owner, table)] tu cac dong TABLE."""
    out = []
    for raw in _lines(path):
        m = RE_TABLE.match(raw.strip())
        if m:
            out.append((m.group(1).upper(), m.group(2).upper()))
    return out


def parse_replicat(path: Path) -> list[dict]:
    """Tra ve [{src_owner, src_table, tgt_owner, tgt_table, keycols, hc}]."""
    out = []
    for raw in _lines(path):
        line = raw.strip()
        m = RE_MAP.match(line)
        if not m:
            continue
        kc = RE_KEYCOLS.search(line)
        out.append({
            "src_owner": m.group(1).upper(), "src_table": m.group(2).upper(),
            "tgt_owner": m.group(3).upper(), "tgt_table": m.group(4).upper(),
            "keycols": ",".join(x.strip().upper() for x in kc.group(1).split(",")) if kc else None,
            "hc": 1 if RE_HC.search(line) else 0,
        })
    return out


async def sync(cfg, con: sqlite3.Connection, ora) -> dict:
    """Doc lai 3 nguon, cap nhat registry, tra ve ket qua doi chieu."""
    now = int(time.time())
    rows: dict[tuple[str, str], dict] = {}

    for sid, st in cfg.streams.items():
        # Luong LOG ghi vao danh sach RIENG (managed_log). Dua no vao day thi
        # mot bang VUA dong bo thuong VUA ghi log se bi doi `stream` qua lai
        # giua hai luong moi lan sync, va dich bi ghi thanh <TEN>_LOG.
        if la_luong_log(st):
            continue
        for owner, tbl in parse_extract(cfg.param_file(st["extract"])):
            r = rows.setdefault((owner, tbl), {
                "src_owner": owner, "src_table": tbl, "tgt_owner": "ODS",
                "tgt_table": tbl, "stream": sid, "keycols": None,
                "in_extract": 0, "has_map": 0, "handlecollisions": 0})
            r["in_extract"] = 1
            r["stream"] = sid
        for m in parse_replicat(cfg.param_file(st["replicat"])):
            key = (m["src_owner"], m["src_table"])
            r = rows.setdefault(key, {
                "src_owner": m["src_owner"], "src_table": m["src_table"],
                "tgt_owner": m["tgt_owner"], "tgt_table": m["tgt_table"],
                "stream": sid, "keycols": None,
                "in_extract": 0, "has_map": 0, "handlecollisions": 0})
            r["has_map"] = 1
            r["tgt_owner"] = m["tgt_owner"]
            r["tgt_table"] = m["tgt_table"]
            r["keycols"] = m["keycols"]
            r["handlecollisions"] = m["hc"]
            r["stream"] = sid

    # Nguon su that thu 3: da bat TRANDATA o nguon chua
    prepared: set[tuple[str, str]] = set()
    try:
        for p in await ora.rows(
                "SELECT table_owner, table_name FROM"
                " dba_capture_prepared_tables@DBL_PMNV"):
            prepared.add((str(p["table_owner"]).upper(), str(p["table_name"]).upper()))
    except Exception as e:
        log.warning("không đọc được danh sách bảng đã bật TRANDATA ở nguồn: %s", e)

    for key in prepared:
        r = rows.setdefault(key, {
            "src_owner": key[0], "src_table": key[1], "tgt_owner": "ODS",
            "tgt_table": key[1], "stream": None, "keycols": None,
            "in_extract": 0, "has_map": 0, "handlecollisions": 0})
    for key, r in rows.items():
        r["prepared"] = 1 if key in prepared else 0

    con.executemany("""
        INSERT INTO managed_table
          (src_owner,src_table,tgt_owner,tgt_table,stream,keycols,
           in_extract,has_map,prepared,handlecollisions,updated_at)
        VALUES (:src_owner,:src_table,:tgt_owner,:tgt_table,:stream,:keycols,
                :in_extract,:has_map,:prepared,:handlecollisions,:ts)
        ON CONFLICT(src_owner,src_table) DO UPDATE SET
          tgt_owner=excluded.tgt_owner, tgt_table=excluded.tgt_table,
          stream=COALESCE(excluded.stream, managed_table.stream),
          keycols=excluded.keycols, in_extract=excluded.in_extract,
          has_map=excluded.has_map, prepared=excluded.prepared,
          handlecollisions=excluded.handlecollisions, updated_at=excluded.updated_at
    """, [dict(r, ts=now) for r in rows.values()])

    # Bang khong con trong bat ky nguon nao -> danh dau, KHONG xoa
    # (xoa se mat lich su verify tro den no)
    con.execute("""UPDATE managed_table SET in_extract=0, has_map=0, prepared=0,
                     note='khong con trong param file lan prepared', updated_at=?
                    WHERE updated_at < ? AND note IS NULL""", (now, now))

    # Bang DA du ca 3 nguon thi xoa ghi chu cu di.
    # Ly do cu the: dong dang ky moi (REGISTRY_ADD) PHAI co `note` de khong
    # bi cau UPDATE ngay tren ghi de - nhung ghi chu do noi "ung vien moi,
    # chua vao luong", va khi bang DA vao luong thi no thanh SAI. Mot ghi chu
    # sai tren trang doi chieu te hon la khong co ghi chu.
    con.execute("""UPDATE managed_table SET note=NULL
                    WHERE in_extract=1 AND has_map=1 AND prepared=1
                      AND note IS NOT NULL""")
    sync_log(cfg, con, now)
    return reconcile(con)


def la_luong_log(st: dict) -> bool:
    return str((st or {}).get("loai", "")).lower() == "log"


def sync_log(cfg, con: sqlite3.Connection, now: int | None = None) -> None:
    """Doc lai file tham so cua cac LUONG LOG, cap nhat managed_log.

    Chi cap nhat co in_extract / has_map / stream / dich. KHONG dung toi
    `prepared` (o day nghia la da bat TRANDATA ALLCOLS - buoc bat TRANDATA
    kiem va ghi, sync khong doc duoc dieu do tu file tham so).
    """
    now = now or int(time.time())
    for sid, st in cfg.streams.items():
        if not la_luong_log(st):
            continue
        ext = set(parse_extract(cfg.param_file(st["extract"])))
        maps = {(m["src_owner"], m["src_table"]): m
                for m in parse_replicat(cfg.param_file(st["replicat"]))}
        for key in ext | set(maps):
            m = maps.get(key) or {}
            con.execute("""
                INSERT INTO managed_log(src_owner,src_table,tgt_owner,tgt_table,
                                        stream,in_extract,has_map,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(src_owner,src_table) DO UPDATE SET
                  tgt_owner=excluded.tgt_owner, tgt_table=excluded.tgt_table,
                  stream=excluded.stream, in_extract=excluded.in_extract,
                  has_map=excluded.has_map, updated_at=excluded.updated_at
            """, (key[0], key[1], m.get("tgt_owner", "ODS"),
                  m.get("tgt_table", f"{key[1]}_LOG"), sid,
                  1 if key in ext else 0, 1 if m else 0, now))
    # Bang khong con trong luong log nao -> ha co, GIU dong (lich su).
    con.execute("UPDATE managed_log SET in_extract=0, has_map=0"
                " WHERE updated_at < ? AND (in_extract=1 OR has_map=1)", (now,))


def reconcile(con: sqlite3.Connection) -> dict:
    """Doi chieu, tra ve cac nhom lech. Tat ca deu la van de THAT."""
    def q(where: str) -> list[dict]:
        return [dict(r) for r in con.execute(
            "SELECT src_owner,src_table,tgt_table,stream,keycols,in_extract,"
            "has_map,prepared,handlecollisions FROM managed_table WHERE " + where
            + " ORDER BY stream, src_table")]

    return {
        # CAI BAY SO MOT: Extract dao nhung Replicat bo qua -> mat im lang
        "capture_khong_map": q("in_extract=1 AND has_map=0"),
        # MAP ma chua bat TRANDATA -> Replicat cho mai, hoac khoa suy luan sai
        "map_khong_prepared": q("has_map=1 AND prepared=0"),
        # Da bat TRANDATA nhung Extract khong lay -> dao redo vo ich o nguon
        # ... tru bang dang nam trong mot LUONG LOG: TRANDATA cua no dang
        # duoc Extract cua luong log dung, khong phai dao redo vo ich.
        "prepared_khong_extract": q(
            "prepared=1 AND in_extract=0 AND src_table NOT IN"
            " (SELECT src_table FROM managed_log WHERE in_extract=1)"),
        # Con HANDLECOLLISIONS sot lai -> dang che loi that
        "con_handlecollisions": q("handlecollisions=1"),
        # Thieu KEYCOLS tuong minh -> GG tu suy luan, rui ro voi bang khong PK
        "thieu_keycols": q("has_map=1 AND (keycols IS NULL OR keycols='')"),
        "tong": con.execute(
            "SELECT COUNT(*) FROM managed_table WHERE in_extract=1 OR has_map=1"
        ).fetchone()[0],
        "day_du": con.execute(
            "SELECT COUNT(*) FROM managed_table"
            " WHERE in_extract=1 AND has_map=1 AND prepared=1").fetchone()[0],
        # So bang theo tung luong cho the luong o trang Tong quan.
        # Dem rieng Extract va MAP: hai so lech nhau la dau hieu cai bay so mot.
        "theo_luong": {
            r["stream"]: dict(r) for r in con.execute(
                "SELECT stream, COUNT(*) tong, SUM(in_extract) n_extract,"
                " SUM(has_map) n_map FROM managed_table"
                " WHERE (in_extract=1 OR has_map=1) AND stream IS NOT NULL"
                " GROUP BY stream")},
    }


def listing(con: sqlite3.Connection, stream: str | None = None) -> list[dict]:
    """Danh sach cho trang /tables, kem ket qua verify gan nhat."""
    sql = """
      SELECT m.*, v.started_at v_ts, v.matched v_matched, v.mode v_mode,
             v.src_count v_src, v.tgt_count v_tgt, v.scn v_scn
        FROM managed_table m
        LEFT JOIN verify_run v ON v.id = (
             SELECT id FROM verify_run w WHERE w.src_table = m.src_table
              ORDER BY w.started_at DESC LIMIT 1)
       WHERE (m.in_extract=1 OR m.has_map=1 OR m.prepared=1)
    """
    binds: tuple = ()
    if stream:
        sql += " AND m.stream = ?"
        binds = (stream,)
    sql += " ORDER BY m.stream, m.src_table"
    out = [dict(r) for r in con.execute(sql, binds)]
    # Bang cua LUONG LOG nam o danh sach RIENG (managed_log). Truoc
    # 2026-10-27 trang nay chi doc managed_table nen nut loc "LOG" luon rong.
    # Khong co verify_run / so dong cho bang log: dich la lich su, khong doi
    # chieu 1-1 voi nguon duoc.
    sql = ("SELECT * FROM managed_log"
           " WHERE (in_extract=1 OR has_map=1 OR prepared=1)")
    if stream:
        sql += " AND stream = ?"
    # Du MOI khoa ma tables.html doc: template chay StrictUndefined, thieu
    # src_rows la ca trang 500 (da xay ra ngay lan deploy dau, 2026-10-27).
    rong = {k: None for k in out[0]} if out else {}
    rong.update(src_rows=None, tgt_rows=None, v_ts=None, v_matched=None,
                v_mode=None, v_src=None, v_tgt=None, v_scn=None)
    out += [{**rong, **dict(r), "la_log": True} for r in con.execute(sql, binds)]
    out.sort(key=lambda r: (r.get("stream") or "", r["src_table"]))
    return out


# =====================================================================
# DANG KY NHIEU BANG MOT LAN (2026-10-23)
#
# Truoc day o dang ky chi nhan MOT ten: mot lo ADD_TABLE 20 bang la 20 lan
# arm/fire. Lo PMNV3 231 bang phai dang ky ngoai web bang script. Gio o nhap
# nhan ca danh sach (dau phay / khoang trang / xuong dong), van MOT dong vet
# thao tac cho ca danh sach.
#
# LUAT: tat ca hoac khong. Chi mot ten khong co that o nguon la tu choi CA
# danh sach - danh sach nay la allowlist chong SQL injection, nhan mot nua
# roi bao loi mot nua thi nguoi dung de bo sot ma tuong da xong. Ten DA dang
# ky thi bo qua va bao lai (dan lai danh sach cu khong phai la loi).
# =====================================================================
DANG_KY_TOI_DA = 300


class DangKyError(ValueError):
    pass


async def dang_ky_nhieu(con: sqlite3.Connection, ora, ds: list[str], *,
                        stream: str | None, who: str) -> dict:
    """Dang ky `ds` vao managed_table. Tra {"moi", "da_co", "co_dich"}."""
    from .adapters.ora import check_ident
    if not ds:
        raise DangKyError("chưa nhập tên bảng nào")
    if len(ds) > DANG_KY_TOI_DA:
        raise DangKyError(f"{len(ds)} bảng - tối đa {DANG_KY_TOI_DA} bảng mỗi lần")
    ds = list(dict.fromkeys(check_ident(t.strip().upper()) for t in ds))

    da_co = [t for t in ds if con.execute(
        "SELECT 1 FROM managed_table WHERE src_table=?", (t,)).fetchone()]
    moi = [t for t in ds if t not in da_co]
    if not moi:
        return {"moi": [], "da_co": da_co, "co_dich": set()}

    def _in(sql: str, ten: list[str]) -> str:
        return sql + " AND table_name IN (%s)" % ", ".join(
            f":t{i}" for i in range(len(ten)))

    b = {f"t{i}": t for i, t in enumerate(moi)}
    nguon = {r["table_name"] for r in await ora.rows(_in(
        "SELECT table_name FROM dba_tables@DBL_PMNV WHERE owner='KT_BSH'", moi), **b)}
    thieu = [t for t in moi if t not in nguon]
    if thieu:
        raise DangKyError(
            f"{len(thieu)} bảng KHÔNG tồn tại ở nguồn KT_BSH: {', '.join(thieu)}. "
            "Không đăng ký bảng nào. Danh sách quản lý chỉ nhận bảng có thật ở "
            "nguồn - đây là chốt chặn cuối chống SQL injection.")
    co_dich = {r["table_name"] for r in await ora.rows(_in(
        "SELECT table_name FROM dba_tables WHERE owner='ODS'", moi), **b)}

    now = int(time.time())
    con.execute("BEGIN")
    try:
        for t in moi:
            # `note` PHAI co: sync() chi ghi de dong co note IS NULL.
            con.execute(
                "INSERT INTO managed_table(src_owner,src_table,tgt_owner,tgt_table,"
                "stream,in_extract,has_map,prepared,handlecollisions,note,updated_at)"
                " VALUES ('KT_BSH',?,'ODS',?,?,0,0,0,0,?,?)",
                (t, t, stream or None,
                 f"mới đăng ký bởi {who} - chưa vào luồng nào", now))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"moi": moi, "da_co": da_co, "co_dich": co_dich}


# =====================================================================
# BO DANG KY (2026-10-23)
#
# Chi XOA DONG trong managed_table cua bang CHUA THUOC LUONG NAO. Khong cham
# GoldenGate, khong cham bang dich, khong cham TRANDATA o nguon. Truoc day
# khong co duong nay: bang dang ky nham hoac bi loai khoi lo (vd 5 bang khong
# khoa cua lo PMNV2 #40) nam mai trong o "them bang vao luong".
#
# Tu choi (tat ca hoac khong):
#   - bang da co TABLE hoac MAP  -> dang thuoc luong, phai di quy trinh go bang
#   - bang dang bat TRANDATA      -> sync() doc dba_capture_prepared_tables va
#     THEM LAI dong nay o lan sync ke tiep. Xoa roi de no am tham quay lai la
#     noi doi nguoi dung; muon bo han thi phai tat TRANDATA (ngoai pham vi).
#   - bang nam trong phien quy trinh dang MO
# =====================================================================
class BoDangKyError(ValueError):
    pass


def bo_dang_ky_nhieu(con: sqlite3.Connection, ds: list[str], *,
                     chi_kiem: bool = False, tat_trandata: bool = False
                     ) -> list[dict]:
    """Xoa cac dong `ds` khoi managed_table. Tra lai cac dong da (se) xoa.

    `chi_kiem=True`: chi kiem dieu kien, khong xoa - dung luc arm.
    `tat_trandata=True`: NHAN bang con bat TRANDATA, vi ben goi se tat no
    TRUOC khi xoa (xem main.py REGISTRY_DEL). Dong van chi bi xoa khi cot
    `prepared` da ve 0 - ben goi chi dat ve 0 sau khi DOC LAI thay da tat.
    """
    import json
    if not ds:
        raise BoDangKyError("chưa nhập tên bảng nào")
    if len(ds) > DANG_KY_TOI_DA:
        raise BoDangKyError(f"{len(ds)} bảng - tối đa {DANG_KY_TOI_DA} bảng mỗi lần")
    ds = list(dict.fromkeys(t.strip().upper() for t in ds))

    mo: dict[str, int] = {}
    for r in con.execute("SELECT id, src_table, src_tables FROM wizard_run"
                         " WHERE state='OPEN'"):
        try:
            ten = json.loads(r["src_tables"] or "[]") or [r["src_table"]]
        except (TypeError, ValueError):
            ten = [r["src_table"]]
        for t in ten:
            mo[str(t).upper()] = r["id"]

    dong, loi = [], []
    for t in ds:
        r = con.execute("SELECT * FROM managed_table WHERE src_table=?",
                        (t,)).fetchone()
        if r is None:
            loi.append(f"{t}: không có trong danh sách quản lý")
        elif r["in_extract"] or r["has_map"]:
            loi.append(f"{t}: đang thuộc luồng {r['stream'] or '?'} (có "
                       f"{'TABLE' if r['in_extract'] else ''}"
                       f"{' + ' if r['in_extract'] and r['has_map'] else ''}"
                       f"{'MAP' if r['has_map'] else ''}) - dùng quy trình gỡ bảng")
        elif r["prepared"] and not tat_trandata:
            loi.append(f"{t}: TRANDATA ở nguồn vẫn bật - lần đồng bộ danh sách kế "
                       "tiếp sẽ tự thêm lại bảng này. Tích ô «tắt TRANDATA ở nguồn» để tắt luôn")
        elif t in mo:
            loi.append(f"{t}: nằm trong quy trình #{mo[t]} đang mở - bỏ phiên trước")
        else:
            dong.append(dict(r))
    if loi:
        raise BoDangKyError("Không bỏ đăng ký bảng nào. " + " | ".join(loi))
    if chi_kiem:
        return dong

    con.execute("BEGIN")
    try:
        for r in dong:
            con.execute("DELETE FROM managed_table WHERE src_owner=? AND src_table=?"
                        " AND in_extract=0 AND has_map=0 AND prepared=0",
                        (r["src_owner"], r["src_table"]))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return dong


def bang_trong_extract(cfg) -> dict[str, str]:
    """{TEN_BANG: EXTRACT} cua MOI Extract trong cfg.streams - KE CA luong LOG.

    Dung truoc khi tat TRANDATA: managed_table khong chua bang cua luong LOG
    (xem sync), nen co in_extract=0 o day KHONG du de ket luan bang khong con
    duoc capture. Phai doc thang file tham so cua tung Extract.
    """
    ra: dict[str, str] = {}
    for st in cfg.streams.values():
        for owner, tbl in parse_extract(cfg.param_file(st["extract"])):
            if owner.upper() == "KT_BSH":
                ra[tbl.upper()] = st["extract"]
    return ra


# =====================================================================
# TRA CUU (2026-10-23): ten bang (hoac ca danh sach) -> dang o luong nao,
# Extract nao co dong TABLE, Replicat nao co dong MAP (dich, KEYCOLS,
# HANDLECOLLISIONS), co trong danh sach quan ly / phien dang mo khong.
#
# Doc THANG file tham so cua MOI luong (ke ca luong LOG) chu khong chi doc
# managed_table: managed_table la ban sao cua lan sync gan nhat, con cau hoi
# "bang nay co dang dong bo khong" phai tra loi theo cai GoldenGate DANG doc.
# Chi doc - khong ghi gi.
# =====================================================================
def tra_cuu(cfg, con: sqlite3.Connection, ds: list[str]) -> list[dict]:
    import json
    ext: dict[str, list[dict]] = {}
    rep: dict[str, list[dict]] = {}
    for sid, st in cfg.streams.items():
        loai = "log" if la_luong_log(st) else "thuong"
        for owner, tbl in parse_extract(cfg.param_file(st["extract"])):
            if owner.upper() == "KT_BSH":
                ext.setdefault(tbl.upper(), []).append(
                    {"stream": sid, "extract": st["extract"], "loai": loai})
        for m in parse_replicat(cfg.param_file(st["replicat"])):
            if m["src_owner"] == "KT_BSH":
                rep.setdefault(m["src_table"], []).append(
                    dict(m, stream=sid, replicat=st["replicat"], loai=loai))

    mo: dict[str, list[int]] = {}
    for r in con.execute("SELECT id, src_table, src_tables FROM wizard_run"
                         " WHERE state='OPEN'"):
        try:
            ten = json.loads(r["src_tables"] or "[]") or [r["src_table"]]
        except (TypeError, ValueError):
            ten = [r["src_table"]]
        for t in ten:
            mo.setdefault(str(t).upper(), []).append(r["id"])

    out = []
    for t in dict.fromkeys(x.strip().upper() for x in ds if x.strip()):
        mt = con.execute("SELECT * FROM managed_table WHERE src_table=?", (t,)).fetchone()
        ml = con.execute("SELECT * FROM managed_log WHERE src_table=?", (t,)).fetchone()
        e, r = ext.get(t, []), rep.get(t, [])
        se, sr = {x["stream"] for x in e}, {x["stream"] for x in r}
        if se & sr:
            kl, muc = "ĐANG ĐỒNG BỘ", "ok"
        elif e and not r:
            # Bay so mot cua he: Extract doc ma Replicat bo qua - mat du lieu im lang
            kl, muc = "CÓ TABLE mà KHÔNG có MAP - dữ liệu bị bỏ qua im lặng", "bad"
        elif r and not e:
            kl, muc = "CÓ MAP mà KHÔNG có TABLE - Replicat không nhận được gì", "bad"
        elif e and r:
            kl, muc = "TABLE và MAP nằm ở HAI luồng khác nhau", "bad"
        elif mt or ml:
            kl, muc = "Đã đăng ký, CHƯA vào luồng nào", "warn"
        else:
            kl, muc = "Chưa có trong luồng nào, chưa đăng ký", "muted"
        out.append({"bang": t, "ket_luan": kl, "muc": muc, "extract": e,
                    "replicat": r, "managed": dict(mt) if mt else None,
                    "managed_log": dict(ml) if ml else None,
                    "phien_mo": mo.get(t, [])})
    return out
