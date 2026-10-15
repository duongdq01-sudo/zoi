"""Boc Oracle bang python-oracledb THIN mode.

Vi sao thin: pure Python, khong can thu vien Oracle nao -> tien trinh web
tach hoan toan khoi $ORACLE_HOME, het lo xung dot LD_LIBRARY_PATH (trong
$ORACLE_HOME/lib co libssl/libcrypto ban Oracle). Va thin mode KHONG ho tro
`/ as sysdba` - day la TINH NANG, no buoc cong cu khong the co sysdba.

Phan quyen: 95% request dung GGADM_RO. Nguon PMNV CHI truy cap qua
@DBL_PMNV, va la READ-ONLY tuyet doi voi cong cu nay.

CHONG SQL INJECTION - 5 lop, vi KHONG the bind *identifier*:
 1. schema la HANG SO trong code, UI khong co o nhap schema
 2. allowlist tu catalog: ten nguoi dung chon chi dung lam BIND VALUE de tra
    dba_tables; neu co thi ghep vao SQL dong bang chuoi ORACLE TRA VE,
    khong bao gio bang chuoi nguoi dung go  <-- lop quan trong nhat
 3. validate cu phap truoc khi toi DB
 4. DBMS_ASSERT o moi cho sinh SQL dong trong PL/SQL
 5. gioi han nghiep vu: ten bang con phai nam trong registry 73 bang
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import oracledb

SCHEMA_SOURCE = "KT_BSH"   # hang so, khong lay tu UI
SCHEMA_TARGET = "ODS"      # hang so, khong lay tu UI
DB_LINK = "DBL_PMNV"

IDENT = re.compile(r"^[A-Z][A-Z0-9_$#]{0,29}$")


class BadIdentifier(ValueError):
    pass


def check_ident(v: str) -> str:
    """Lop 3: validate cu phap TRUOC khi gui query nao."""
    if not isinstance(v, str) or not IDENT.match(v):
        raise BadIdentifier(f"Tên đối tượng không hợp lệ: {v!r}")
    return v


class Ora:
    def __init__(self, cfg, cred_name: str = "ggadm_ro"):
        self._cfg = cfg
        cred = cfg.creds.get(cred_name)
        if cred is None:
            raise SystemExit(f"Thiếu khối [{cred_name}] trong conf/secrets.ini")
        self._cred = cred
        self._pool: oracledb.ConnectionPool | None = None

    def _ensure(self) -> oracledb.ConnectionPool:
        if self._pool is None:
            self._pool = oracledb.create_pool(
                user=self._cred.user, password=self._cred.password,
                dsn=self._cred.dsn, min=1, max=4, increment=1,
                config_dir=str(self._cfg.tns_admin),
                getmode=oracledb.POOL_GETMODE_WAIT, timeout=120,
            )
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close(force=True)
            self._pool = None

    # ------------------------------------------------------------ truy van
    def _rows_sync(self, sql: str, **binds: Any) -> list[dict[str, Any]]:
        with self._ensure().acquire() as con, con.cursor() as cur:
            cur.execute(sql, binds)
            if cur.description is None:
                return []
            cols = [d[0].lower() for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    async def rows(self, sql: str, **binds: Any) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._rows_sync, sql, **binds)

    async def one(self, sql: str, **binds: Any) -> dict[str, Any] | None:
        r = await self.rows(sql, **binds)
        return r[0] if r else None

    async def scalar(self, sql: str, **binds: Any) -> Any:
        r = await self.one(sql, **binds)
        return next(iter(r.values())) if r else None

    # ----------------------------------------------- lop 2: allowlist catalog
    async def resolve_target_table(self, name: str) -> str:
        """Tra ve ten bang DO ORACLE PHAT RA, khong phai chuoi nguoi dung go."""
        check_ident(name)
        r = await self.one(
            "SELECT table_name FROM dba_tables WHERE owner=:o AND table_name=:t",
            o=SCHEMA_TARGET, t=name)
        if not r:
            raise BadIdentifier(f"Không có bảng {SCHEMA_TARGET}.{name} trong danh mục của DB")
        return r["table_name"]

    # ------------------------------------------------------------ giam sat
    async def capture_status(self) -> list[dict[str, Any]]:
        """Capture o NGUON qua DB link.

        dba_capture o 19c KHONG co cot capture_message_number (cam bay 44).
        Dung required_checkpoint_scn / captured_scn / applied_scn.
        """
        return await self.rows(f"""
            SELECT capture_name, status, required_checkpoint_scn, captured_scn,
                   applied_scn, last_enqueued_scn, error_number, error_message
              FROM dba_capture@{DB_LINK} ORDER BY capture_name""")

    async def apply_status(self) -> list[dict[str, Any]]:
        """Apply o DICH.

        KHONG dung dba_apply_progress.applied_message_number: GG integrated
        apply khong cap nhat cot do, no luon tra 0 -> de tuong apply da chet
        (cam bay 45). Tien do that lay tu checkpoint table.
        """
        return await self.rows("""
            SELECT apply_name, status, error_number, error_message
              FROM dba_apply ORDER BY apply_name""")

    async def replicat_checkpoints(self, replicats: list[str]) -> list[dict[str, Any]]:
        """Tien do apply THAT, tu checkpoint table OGG_BSH.<R>.

        seqno/rba phai tien len va KHONG duoc ve 0. Day la bang chung manh
        nhat de biet Replicat dang apply that chu khong chi hien RUNNING.
        """
        out = []
        for r in replicats:
            check_ident(r)
            try:
                row = await self.one(
                    f'SELECT group_name, seqno, rba, log_bsn, log_csn,'
                    f' last_update_ts FROM ogg_bsh."{r}"')
            except oracledb.DatabaseError as e:
                out.append({"group_name": r, "err": str(e).splitlines()[0]})
                continue
            if row:
                out.append(row)
        return out

    async def prepared_tables(self) -> int:
        return int(await self.scalar(
            f"SELECT COUNT(*) FROM dba_capture_prepared_tables@{DB_LINK}") or 0)

    async def source_scn(self) -> int:
        return int(await self.scalar(f"SELECT current_scn FROM v$database@{DB_LINK}") or 0)

    async def target_scn(self) -> int:
        return int(await self.scalar("SELECT current_scn FROM v$database") or 0)

    async def tablespace_room(self) -> list[dict[str, Any]]:
        """Room theo tablespace VA theo TUNG datafile.

        Phai tinh tung datafile: tablespace bao "con cho" trong khi datafile
        da cung tran 32 GB cua smallfile la cai bay kinh dien (ORA-03206).
        """
        return await self.rows("""
            SELECT d.tablespace_name, d.file_name, d.file_id,
                   ROUND(d.bytes/1048576)                      size_mb,
                   ROUND(d.maxbytes/1048576)                   max_mb,
                   ROUND((d.maxbytes - d.bytes + NVL(f.free,0))/1048576) room_mb,
                   d.autoextensible
              FROM dba_data_files d
              LEFT JOIN (SELECT file_id, SUM(bytes) free
                           FROM dba_free_space GROUP BY file_id) f
                     ON f.file_id = d.file_id
             ORDER BY d.tablespace_name, d.file_id""")

    async def undo_retention_source(self) -> int:
        """tuned_undoretention o NGUON - quyet dinh 1 SCN/bang hay phai chia lo."""
        return int(await self.scalar(f"""
            SELECT tuned_undoretention FROM v$undostat@{DB_LINK}
             ORDER BY begin_time DESC FETCH FIRST 1 ROWS ONLY""") or 0)

    # ------------------------------------------------------------- verify
    async def checksum_expr(self, table: str) -> str:
        """Bieu thuc checksum doc lap NLS: hash TUNG COT roi noi bang '|'.

        Chiu duoc bang rong. Phai LUU LAI bieu thuc cung ket qua verify:
        param co DDL INCLUDE MAPPED -> DDL them cot -> bieu thuc doi -> hash
        doi MA KHONG PHAI vi du lieu lech.
        """
        t = await self.resolve_target_table(table)
        rows = await self.rows(
            "SELECT column_name FROM dba_tab_columns"
            " WHERE owner=:o AND table_name=:t ORDER BY column_id",
            o=SCHEMA_TARGET, t=t)
        if not rows:
            raise BadIdentifier(f"Bảng {t} không có cột nào")
        parts = [f'NVL(ORA_HASH("{r["column_name"]}"),0)' for r in rows]
        return "||'|'||".join(parts)

    async def count_and_hash_target(self, table: str) -> tuple[int, str]:
        t = await self.resolve_target_table(table)
        expr = await self.checksum_expr(t)
        r = await self.one(
            f'SELECT COUNT(*) c, NVL(SUM(ORA_HASH({expr})),0) h'
            f' FROM {SCHEMA_TARGET}."{t}"')
        return int(r["c"]), str(r["h"])

    async def count_and_hash_source(self, table: str, scn: int | None = None
                                    ) -> tuple[int, str]:
        """scn=None -> doc hien tai. Co scn -> flashback AS OF SCN.

        AS OF SCN KHONG nhan subquery (ORA-22818, cam bay 48) nen SCN phai
        la BIND VARIABLE, khong duoc nhung `(SELECT current_scn FROM ...)`.
        """
        t = await self.resolve_target_table(table)   # 73 bang trung ten 2 dau
        expr = await self.checksum_expr(t)
        asof = " AS OF SCN :scn" if scn is not None else ""
        sql = (f'SELECT COUNT(*) c, NVL(SUM(ORA_HASH({expr})),0) h'
               f' FROM {SCHEMA_SOURCE}."{t}"@{DB_LINK}{asof}')
        r = await self.one(sql, **({"scn": scn} if scn is not None else {}))
        return int(r["c"]), str(r["h"])
