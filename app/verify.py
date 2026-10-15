"""Verify doi chieu nguon/dich.

HAI CHE DO, khac nhau ve do tin cay - UI phai noi ro:

  LIVE          : khong dung gi. Doc High WM cua Replicat DANG CHAY roi so
                  COUNT(*) + checksum. CO THE DUONG TINH GIA vi dich van
                  dang thay doi trong luc do. Dung de soi nhanh.
  SNAPSHOT_SCN  : quiesce dung quy trinh - stop extract, cho drain (RBA =
                  kich thuoc trail VA lag 00:00:00), stop replicat, doc
                  HIGH WM, so sanh, roi start lai. Chinh xac.
                  Phase 1 CHUA lam che do nay vi no phai DUNG process.

CONG THUC lay dung cai da chay 8 lo: COUNT(*) + SUM(ORA_HASH(...)) voi bieu
thuc sinh bang LISTAGG hash TUNG COT roi noi bang '|' - doc lap NLS, chiu
duoc bang rong.

BA LUAT KHONG DUOC PHA:
 1. SCN khi verify phai la HIGH Watermark, khong bao gio Low WM hay
    current_scn - ca hai cho duong tinh gia.
 2. `AS OF SCN` KHONG nhan subquery (ORA-22818) -> SCN phai la bind variable.
 3. Phai LUU hash_expr_sha256: param co `DDL INCLUDE MAPPED` nen DDL them
    cot -> bieu thuc doi -> hash doi MA KHONG PHAI vi du lieu lech.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time

from .adapters import gg
from .adapters.ora import SCHEMA_SOURCE, SCHEMA_TARGET

log = logging.getLogger("ggadmin.verify")

# Toi da MOT job verify tren toan he. Ly do: RAM available chi ~6 GB va
# ORA_HASH tren bang 256 trieu dong ngon PGA dang ke - de nhieu job song
# song thi chinh cong cu giam sat gay ra su co no dang giam sat.
_LOCK = asyncio.Lock()


class Busy(RuntimeError):
    pass


class Verifier:
    def __init__(self, cfg, con: sqlite3.Connection, ora, collector=None,
                 control=None):
        self.cfg = cfg
        self.con = con
        self.ora = ora
        self.collector = collector
        self.control = control

    # ------------------------------------------------------------ tien ich
    def is_busy(self) -> bool:
        return _LOCK.locked()

    def _new_job(self, kind: str, who: str, params: str) -> int:
        cur = self.con.execute(
            "INSERT INTO job(kind,state,created_at,created_by,params_json)"
            " VALUES (?,'QUEUED',?,?,?)", (kind, int(time.time()), who, params))
        return int(cur.lastrowid)

    def _job(self, job_id: int, **kw) -> None:
        if not kw:
            return
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        self.con.execute(f"UPDATE job SET {sets} WHERE id=:id", dict(kw, id=job_id))

    def job_state(self, job_id: int) -> dict | None:
        r = self.con.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def running_job(self) -> dict | None:
        r = self.con.execute(
            "SELECT * FROM job WHERE state IN ('QUEUED','RUNNING')"
            " ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    # ---------------------------------------------------------------- SCN
    async def high_wm(self, replicat: str) -> int | None:
        """HIGH Watermark CSN cua Replicat. KHONG dung Low WM hay current_scn."""
        d = await gg.detail(self.cfg, replicat)
        v = d.get("high_wm")
        return int(v) if v else None

    # ------------------------------------------------------------- mot bang
    async def verify_table(self, src_table: str, *, scn: int | None,
                           mode: str, stream: str | None, batch_id: int | None,
                           who: str) -> dict:
        t0 = time.monotonic()
        started = int(time.time())
        row = self.con.execute(
            "SELECT src_owner,src_table,tgt_owner,tgt_table FROM managed_table"
            " WHERE src_table=?", (src_table,)).fetchone()
        if row is None:
            raise ValueError(f"{src_table} không nằm trong danh sách quản lý -> từ chối")
        tgt = row["tgt_table"]

        cur = self.con.execute(
            "INSERT INTO verify_run(batch_id,started_at,mode,scn,stream,"
            "src_table,tgt_table,run_by) VALUES (?,?,?,?,?,?,?,?)",
            (batch_id, started, mode, scn, stream, src_table, tgt, who))
        vid = int(cur.lastrowid)

        try:
            expr = await self.ora.checksum_expr(tgt)
            sha = hashlib.sha256(expr.encode()).hexdigest()
            ncol = expr.count("ORA_HASH(")
            # Doc DICH truoc roi NGUON: voi che do LIVE thi khoang cach giua
            # hai phep dem cang nho cang it duong tinh gia.
            tc, th = await self.ora.count_and_hash_target(tgt)
            sc, sh = await self.ora.count_and_hash_source(tgt, scn)
            ok = 1 if (sc == tc and sh == th) else 0
            self.con.execute(
                "UPDATE verify_run SET finished_at=?,src_count=?,tgt_count=?,"
                "src_hash=?,tgt_hash=?,matched=?,col_count=?,"
                "hash_expr_sha256=?,duration_ms=? WHERE id=?",
                (int(time.time()), sc, tc, sh, th, ok, ncol, sha,
                 int((time.monotonic() - t0) * 1000), vid))
            self.con.execute(
                "UPDATE managed_table SET src_rows=?, tgt_rows=? WHERE src_table=?",
                (sc, tc, src_table))
            return {"id": vid, "src_table": src_table, "tgt_table": tgt,
                    "src_count": sc, "tgt_count": tc, "src_hash": sh,
                    "tgt_hash": th, "matched": ok, "col_count": ncol}
        except Exception as e:
            msg = str(e).splitlines()[0][:400]
            self.con.execute(
                "UPDATE verify_run SET finished_at=?,err_msg=?,duration_ms=?"
                " WHERE id=?",
                (int(time.time()), msg, int((time.monotonic() - t0) * 1000), vid))
            return {"id": vid, "src_table": src_table, "tgt_table": tgt,
                    "matched": None, "err": msg}

    # ------------------------------------------------------- mot lo (job)
    async def run_batch(self, tables: list[str], *, mode: str, who: str,
                        stream: str | None = None) -> int:
        """Tao job va chay nen. Tra job_id ngay de UI poll."""
        if _LOCK.locked():
            raise Busy("đang có một việc đối chiếu chạy - tối đa 1 việc trên toàn hệ")
        job_id = self._new_job(
            f"VERIFY_{mode}", who,
            f'{{"stream": {stream!r}, "so_bang": {len(tables)}}}')
        asyncio.create_task(
            self._batch_task(job_id, tables, mode, who, stream),
            name=f"verify-{job_id}")
        return job_id

    async def _batch_task(self, job_id: int, tables: list[str], mode: str,
                          who: str, stream: str | None) -> None:
        async with _LOCK:
            self._job(job_id, state="RUNNING", started_at=int(time.time()),
                      batch_id=job_id, progress_pct=0.0,
                      progress_note="đang đọc mốc High Watermark")
            quiesced = False
            try:
                if mode == "SNAPSHOT_SCN":
                    # Che do CHUAN: dong bang dich lai roi moi do. Phai chay
                    # theo LUONG vi no dung dung Extract/Replicat cua luong do.
                    if not stream or stream not in self.cfg.streams:
                        raise RuntimeError(
                            "chế độ chuẩn phải chạy theo luồng - nó dừng Extract và Replicat của "
                            "chính luồng đó")
                    if self.control is None:
                        raise RuntimeError("thiếu phần điều khiển tiến trình, không dừng tạm được luồng")
                    self._job(job_id, progress_note="dừng tạm luồng: dừng Extract -> chờ chảy hết -> dừng Replicat")
                    q = await self.control.quiesce(stream)
                    quiesced = True
                    log.info("quiesce %s: %s", stream, q["buoc"])

                # Doc High WM cua Replicat cua luong. Voi nhieu luong thi moi
                # luong mot SCN -> o day chi lam MOT luong moi lan.
                scn = None
                if stream and stream in self.cfg.streams:
                    scn = await self.high_wm(self.cfg.streams[stream]["replicat"])
                if scn is None:
                    # Khong doc duoc High WM -> KHONG verify. Verify o SCN sai
                    # con te hon khong verify.
                    raise RuntimeError(
                        "không đọc được mốc High Watermark -> dừng, không đối chiếu")
                self._job(job_id, progress_note=f"SCN {scn}"
                          + (" (đích đang đứng yên)" if quiesced else ""))

                lech: list[str] = []
                loi: list[str] = []
                n = len(tables)
                for i, t in enumerate(tables, 1):
                    r = await self.verify_table(
                        t, scn=scn, mode=mode, stream=stream,
                        batch_id=job_id, who=who)
                    if r.get("err"):
                        loi.append(f"{t}: {r['err'][:80]}")
                    elif not r["matched"]:
                        lech.append(t)
                    self._job(job_id, progress_pct=round(i / n * 100, 1),
                              progress_note=f"{i}/{n} - {t}"
                                            + (f" (lệch {len(lech)})" if lech else ""))
                res = (f'{{"so_bang": {n}, "lech": {len(lech)}, "loi": {len(loi)},'
                       f' "scn": {scn}, "ten_lech": {lech!r}, "ten_loi": {loi!r}}}')
                self._job(job_id, state="SUCCEEDED", finished_at=int(time.time()),
                          progress_pct=100.0, result_json=res,
                          progress_note=f"xong: {n - len(lech) - len(loi)}/{n} khớp")
                self._update_verify_alert()
            except Exception as e:
                log.exception("việc đối chiếu %s thất bại", job_id)
                self._job(job_id, state="FAILED", finished_at=int(time.time()),
                          err_msg=str(e).splitlines()[0][:400])
            finally:
                # LUON start lai, du verify that bai hay bi huy. De luong
                # production dung sau mot job do dac la hong nang hon han
                # viec khong do duoc.
                if quiesced and self.control is not None:
                    try:
                        self._job(job_id, progress_note="start lai: replicat "
                                                        "truoc, extract sau")
                        await self.control.unquiesce(stream)
                        log.info("đã chạy lại luồng %s", stream)
                    except Exception:                       # noqa: BLE001
                        log.exception("KHÔNG chạy lại được luồng %s", stream)
                        cu = (self.job_state(job_id) or {}).get("err_msg") or ""
                        self._job(job_id, state="FAILED",
                                  err_msg=(cu + " | CẢNH BÁO: không chạy lại được "
                                           f"duoc luong {stream}, PHAI vao "
                                           "ggsci start tay NGAY")[:400])

    # --------------------------------------------------------- canh bao
    def _update_verify_alert(self) -> None:
        """Mo/dong canh bao `verify_lech` theo ket qua lan verify gan nhat."""
        n = self.con.execute("""
            SELECT COUNT(*) FROM verify_run v
             WHERE v.matched = 0
               AND v.id = (SELECT id FROM verify_run w
                            WHERE w.src_table = v.src_table
                            ORDER BY w.started_at DESC LIMIT 1)""").fetchone()[0]
        rid = self.con.execute(
            "SELECT id FROM alert_rule WHERE name='verify_lech'").fetchone()
        if rid is None:
            return
        rid = rid["id"]
        now = int(time.time())
        cur = self.con.execute(
            "SELECT id FROM alert_event WHERE rule_id=? AND scope_key='ALL'"
            " AND closed_at IS NULL", (rid,)).fetchone()
        if n > 0 and cur is None:
            self.con.execute(
                "INSERT INTO alert_event(rule_id,scope_key,opened_at,peak_val)"
                " VALUES (?,?,?,?)", (rid, "ALL", now, float(n)))
        elif n > 0 and cur is not None:
            self.con.execute("UPDATE alert_event SET peak_val=? WHERE id=?", (float(n), cur["id"]))
        elif n == 0 and cur is not None:
            self.con.execute("UPDATE alert_event SET closed_at=? WHERE id=?", (now, cur["id"]))

    # ------------------------------------------------------------- bao cao
    def last_batch(self, job_id: int | None = None) -> list[dict]:
        if job_id is None:
            r = self.con.execute(
                "SELECT MAX(batch_id) b FROM verify_run").fetchone()
            job_id = r["b"] if r else None
        if job_id is None:
            return []
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM verify_run WHERE batch_id=? ORDER BY matched, src_table",
            (job_id,))]
