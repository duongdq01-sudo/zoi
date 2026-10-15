"""Sinh DDL bang dich tu metadata nguon.

VI SAO CAN NO
Wizard `ADD_TABLE` yeu cau bang dich DA TON TAI (buoc KIEM_BANG_DICH). Truoc
day moi lan them bang moi deu phai chay `gen_target_ddl.py` ngoai cong cu roi
sua tay file sinh ra. Day la mot buoc ngoai luong, va la ly do `ADD_TABLE`
chua bao gio chay duoc tron mot lan.

PHAM VI CO Y HEP: module nay chi xu ly cac kieu du lieu THONG THUONG. Gap
kieu can phan xet (LOB, LONG, XMLTYPE, kieu tu dinh nghia, bang partition)
thi NO TU CHOI va chi ra `gen_target_ddl.py`. Sinh bua mot DDL gan dung cho
mot bang 256 trieu dong roi phat hien sai kieu sau khi nap la hong nang hon
han viec tu choi tu dau.

HAI DIEU CO Y KHONG MANG SANG DICH

1. DEFAULT cua cot.
   Dich la bang nhan replicate: GoldenGate cung cap DU gia tri moi cot trong
   moi ban ghi, nen DEFAULT khong bao gio duoc dung den. Mang sang mot
   DEFAULT sai (nhat la cot `data_default` la kieu LONG, da tung gay
   ORA-00932 khi doc) con te hon la khong mang.

2. Constraint ngoai PK (FK, CHECK, UNIQUE phu).
   Dich la bang bao cao, khong phai bang nghiep vu. Mot FK o dich se lam
   Replicat ABEND khi thu tu apply khac thu tu phat sinh o nguon - va thu tu
   do KHONG bao dam. Day la loi se xay ra chac chan, khong phai co the.
   Index thi mang sang (de truy van), PK mang sang (de bat dong trung),
   nhung FK/CHECK thi khong.
"""

from __future__ import annotations

import logging
import time

from .adapters.ora import check_ident

log = logging.getLogger("ggadmin.ddl")

TS_DATA = "BSH_DATA"
TS_INDEX = "BSH_INDEX"

# Kieu xu ly duoc. Ngoai danh sach nay thi tu choi.
KIEU_DON = {"DATE", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE", "ROWID",
            "CLOB", "BLOB", "NCLOB", "LONG", "LONG RAW", "XMLTYPE"}
KIEU_TU_CHOI = {"CLOB", "BLOB", "NCLOB", "LONG", "LONG RAW", "XMLTYPE",
                "BFILE", "ROWID", "UROWID"}


class DdlError(RuntimeError):
    pass


def kieu_cot(c: dict) -> str:
    """Dung lai kieu du lieu cua mot cot tu dba_tab_columns."""
    t = (c["data_type"] or "").upper()
    if t in KIEU_TU_CHOI:
        raise DdlError(
            f'Cột "{c["column_name"]}" kiểu {t} - phần này không xử lý được. Dùng '
            "gen_target_ddl.py và tạo bảng đích bằng tay.")
    if t in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        n = c.get("char_length") or c.get("data_length")
        if not n:
            raise DdlError(f'Cột "{c["column_name"]}" {t} không đọc được độ dài')
        # CHAR_USED = 'C' nghia la khai bao theo KY TU, khong theo byte. Mang
        # sai don vi thi cot bi ngan hon nguon va ORA-12899 luc apply.
        #
        # NHUNG NVARCHAR2/NCHAR thi KHONG duoc them qualifier: do dai cua
        # kieu N* LUON tinh theo ky tu, va Oracle tu choi cu phap
        # `NVARCHAR2(200 CHAR)` bang dung ORA-00907 missing right
        # parenthesis. Ma dba_tab_columns lai tra char_used='C' cho moi cot
        # N*, nen cho chung di chung duong voi VARCHAR2 la sinh ra DDL
        # khong chay duoc. Da gap that 2026-10-15: buoc 3 cua quy trinh
        # them bang HT_MA_KENH_BAN chet voi ORA-00907.
        dv = "" if t.startswith("N") else (
            " CHAR" if (c.get("char_used") or "B") == "C" else "")
        return f"{t}({int(n)}{dv})"
    if t == "NUMBER":
        p, s = c.get("data_precision"), c.get("data_scale")
        if p is None:
            return "NUMBER"
        return f"NUMBER({int(p)})" if not s else f"NUMBER({int(p)},{int(s)})"
    if t == "RAW":
        return f"RAW({int(c['data_length'])})"
    if t.startswith(("TIMESTAMP", "INTERVAL")):
        return t                      # dba_tab_columns da chua do chinh xac
    if t in KIEU_DON:
        return t
    raise DdlError(
        f'Cột "{c["column_name"]}" kiểu {t} - chưa hỗ trợ. Dùng '
        "gen_target_ddl.py và tạo bảng đích bằng tay.")


def sinh_ddl(*, tgt_owner: str, tgt_table: str, cols: list[dict],
             pk: list[dict], idx: list[dict], src: str,
             grantee: str | None = "GGADM_LOAD") -> str:
    """Sinh script tao bang dich + PK + index + grant.

    `idx` la index o NGUON. Index UNIQUE trung cot voi PK bi bo (PK da tao
    index roi). Ten index o dich giu nguyen ten nguon de doi chieu de.
    """
    t = check_ident(tgt_table)
    o = check_ident(tgt_owner)
    if not cols:
        raise DdlError("không có cột nào")

    dong_cot = []
    for c in sorted(cols, key=lambda x: x["column_id"]):
        kieu = kieu_cot(c)
        nn = "" if (c.get("nullable") or "Y") == "Y" else " NOT NULL"
        dong_cot.append(f'  "{c["column_name"]}" {kieu}{nn}')

    pk_cols = [r["column_name"] for r in sorted(pk, key=lambda x: x["position"])]
    out: list[str] = [
        f"-- ggadmin: DDL bang dich {o}.{t}",
        f"-- Sinh tu metadata cua {src} luc "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "--",
        "-- CO Y KHONG mang sang dich:",
        "--   DEFAULT cua cot  -> GoldenGate cung cap du gia tri moi cot",
        "--   FK / CHECK       -> mot FK o dich se lam Replicat ABEND khi thu",
        "--                       tu apply khac thu tu phat sinh o nguon",
        "WHENEVER SQLERROR EXIT FAILURE",
        "",
        f'CREATE TABLE {o}."{t}" (',
        ",\n".join(dong_cot),
        f") TABLESPACE {TS_DATA};",
        "",
    ]
    if pk_cols:
        ten_pk = f"{t}_P"[:30]
        out += [
            f'ALTER TABLE {o}."{t}" ADD CONSTRAINT "{ten_pk}"',
            f'  PRIMARY KEY ({", ".join(chr(34) + c + chr(34) for c in pk_cols)})',
            f"  USING INDEX TABLESPACE {TS_INDEX};",
            "",
        ]
    else:
        out += ["-- Nguon KHONG co PK -> dich khong co PK. Khoa se khai bang",
                "-- KEYCOLS o buoc 2 cua wizard.", ""]

    n_idx = 0
    for x in idx:
        if (x["uniqueness"] == "UNIQUE"
                and [c.strip() for c in x["cols"].split(",")] == pk_cols):
            continue                  # PK da tao index nay
        if "FUNCTION" in (x["index_type"] or ""):
            out.append(f"-- BO QUA index function-based {x['index_name']} "
                       f"({x['cols']}): GoldenGate khong dung duoc lam khoa,")
            out.append("-- va bieu thuc cua no can phan xet. Tao tay neu can.")
            continue
        n_idx += 1
        uq = "UNIQUE " if x["uniqueness"] == "UNIQUE" else ""
        cl = ", ".join(chr(34) + c.strip() + chr(34) for c in x["cols"].split(","))
        out += [f'CREATE {uq}INDEX {o}."{x["index_name"]}"',
                f'  ON {o}."{t}" ({cl}) TABLESPACE {TS_INDEX};', ""]

    if grantee:
        g = check_ident(grantee)
        out += [
            f"-- Grant cho {g}: day cung la cach PHAM VI cua ODS.GGADM_API tu",
            f"-- mo rong sang bang moi (package doc user_tab_privs_made).",
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON {o}."{t}" TO {g};',
            "",
            "-- Grant cho GGADM_RO: BAT BUOC, khong phai tuy chon.",
            "-- Thieu nó thi cong cu TAO RA mot bang ma CHINH NO khong doc duoc:",
            "--   verify -> ORA-00942, dem so dong -> ORA-00942,",
            "--   buoc kiem chung sau khi nap -> ORA-00942 va bao FAIL oan.",
            "-- Da gap that o lan chay ADD_TABLE dau tien (2026-10-14): dot nap",
            "-- 3.355 dong THANH CONG nhung buoc NAP van bi danh dau FAIL vi",
            "-- GGADM_RO khong SELECT duoc bang vua tao.",
            f'GRANT SELECT ON {o}."{t}" TO GGADM_RO;',
            "",
        ]
    out += [f"PROMPT SO_INDEX={n_idx}", "EXIT", ""]
    return chr(10).join(out)
