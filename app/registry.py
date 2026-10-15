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
    return reconcile(con)


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
        "prepared_khong_extract": q("prepared=1 AND in_extract=0"),
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
    return [dict(r) for r in con.execute(sql, binds)]
