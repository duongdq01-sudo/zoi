-- =====================================================================
-- cs9_grant_ogg_bsh_src.sql
--
-- CHAY TREN : DB NGUON  PMNV  (172.16.33.68)
-- CHAY BANG : sqlplus / as sysdba
-- CACH CHAY : sqlplus / as sysdba @cs9_grant_ogg_bsh_src.sql
--
-- MUC DICH
--   Cap quyen UPDATE o muc OBJECT tren dung 2 bang, de chay phep thu
--   no-op UPDATE chot cau hoi: cot KEYCOLS co gia tri NULL thi Replicat
--   co tim thay dong o dich khong?
--     KT_BSH.HT_NSD_PVI
--     KT_BSH.HT_NHOM_QUYEN_PVI
--
-- KHONG cap UPDATE ANY TABLE. Chi 2 bang nay, chi quyen UPDATE va SELECT.
--
-- QUYEN NAY LA TAM THOI. Chay xong phep thu thi go ngay:
--     sqlplus / as sysdba @cs9_revoke_ogg_bsh_src.sql
--
-- Phep thu chay bang:  cs9_test_noop_pvi.sql  (bang chinh OGG_BSH)
-- =====================================================================

SET ECHO        OFF
SET FEEDBACK    OFF
SET VERIFY      OFF
SET PAGESIZE    200
SET LINESIZE    170
SET TRIMSPOOL   ON
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE
WHENEVER OSERROR  EXIT FAILURE

COL grantee   FORMAT A14
COL owner     FORMAT A10
COL table_name FORMAT A22
COL privilege FORMAT A12
COL grantable FORMAT A9
COL thoi_diem FORMAT A22

PROMPT
PROMPT ===================================================================
PROMPT  0. CHAN SAI DB  (ca hai DB deu ten BSHC, phai xet theo host_name)
PROMPT ===================================================================

DECLARE
  v_host VARCHAR2(200);
  v_inst VARCHAR2(30);
  v_db   VARCHAR2(30);
BEGIN
  SELECT i.host_name, i.instance_name, d.name
    INTO v_host, v_inst, v_db
    FROM v$instance i, v$database d;

  DBMS_OUTPUT.PUT_LINE('  host_name     = ' || v_host);
  DBMS_OUTPUT.PUT_LINE('  instance_name = ' || v_inst);
  DBMS_OUTPUT.PUT_LINE('  db name       = ' || v_db);

  -- Cam bay cua du an: DB nguon va DB dich DEU ten BSHC va deu co
  -- SERVICE_NAME=BSHC, nen instance_name/db_name KHONG phan biet duoc.
  -- Chi host_name phan biet duoc: nguon 172.16.33.68, dich 172.16.33.90.
  IF INSTR(v_host, '33-68') = 0
     AND INSTR(v_host, '33.68') = 0
     AND INSTR(UPPER(v_host), 'PMNV') = 0 THEN
    RAISE_APPLICATION_ERROR(-20001,
      'DUNG LAI: host "' || v_host || '" khong phai DB nguon PMNV (33.68). '
      || 'Script nay KHONG duoc chay o DB dich. Khong doi gi ca.');
  END IF;

  IF INSTR(v_host, '33-90') > 0 OR INSTR(v_host, '33.90') > 0 THEN
    RAISE_APPLICATION_ERROR(-20001,
      'DUNG LAI: day la DB DICH (host ' || v_host || '). Khong doi gi ca.');
  END IF;

  DBMS_OUTPUT.PUT_LINE('  [ OK ] dung DB nguon PMNV');
END;
/

PROMPT
PROMPT ===================================================================
PROMPT  1. TIEN DIEU KIEN
PROMPT ===================================================================

DECLARE
  TYPE tlist IS TABLE OF VARCHAR2(30);
  v_t    tlist := tlist('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI');
  v_n    PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_n FROM dba_users WHERE username = 'OGG_BSH';
  IF v_n = 0 THEN
    RAISE_APPLICATION_ERROR(-20002, 'Khong co user OGG_BSH tren DB nay.');
  END IF;
  DBMS_OUTPUT.PUT_LINE('  [ OK ] user OGG_BSH ton tai');

  FOR i IN 1 .. v_t.COUNT LOOP
    SELECT COUNT(*) INTO v_n
      FROM dba_tables
     WHERE owner = 'KT_BSH' AND table_name = v_t(i);
    IF v_n = 0 THEN
      RAISE_APPLICATION_ERROR(-20003,
        'Khong thay bang KT_BSH.' || v_t(i) || ' -- khong cap quyen gi ca.');
    END IF;

    SELECT COUNT(*) INTO v_n
      FROM dba_tab_columns
     WHERE owner = 'KT_BSH' AND table_name = v_t(i) AND column_name = 'PHONG';
    IF v_n = 0 THEN
      DBMS_OUTPUT.PUT_LINE('  [ CANH BAO ] KT_BSH.' || v_t(i)
        || ' khong co cot PHONG -- phep thu se bo qua bang nay');
    ELSE
      DBMS_OUTPUT.PUT_LINE('  [ OK ] KT_BSH.' || v_t(i) || ' co cot PHONG');
    END IF;
  END LOOP;
END;
/

PROMPT
PROMPT  So dong co PHONG IS NULL (day la cai lam Replicat khong tim thay dong):

SELECT 'HT_NSD_PVI' AS table_name,
       COUNT(*)                        AS tong_dong,
       COUNT(*) - COUNT(phong)         AS phong_null
  FROM kt_bsh.ht_nsd_pvi
UNION ALL
SELECT 'HT_NHOM_QUYEN_PVI',
       COUNT(*),
       COUNT(*) - COUNT(phong)
  FROM kt_bsh.ht_nhom_quyen_pvi;

PROMPT
PROMPT ===================================================================
PROMPT  2. QUYEN HIEN CO CUA OGG_BSH  (TRUOC khi cap)
PROMPT ===================================================================

PROMPT
PROMPT  Quyen object tren 2 bang:
SELECT grantee, owner, table_name, privilege, grantable
  FROM dba_tab_privs
 WHERE grantee = 'OGG_BSH'
   AND owner   = 'KT_BSH'
   AND table_name IN ('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI')
 ORDER BY table_name, privilege;

PROMPT
PROMPT  Quyen he thong dang '%ANY TABLE%'  (de thay ro la KHONG co UPDATE ANY TABLE):
SELECT privilege
  FROM dba_sys_privs
 WHERE grantee = 'OGG_BSH'
   AND privilege LIKE '%ANY TABLE%'
 ORDER BY privilege;

PROMPT
PROMPT ===================================================================
PROMPT  3. CAP QUYEN
PROMPT ===================================================================

-- SELECT cap kem de phep thu chon duoc dong mau (tim ROWID co PHONG IS NULL).
-- Thuc te OGG_BSH gan nhu chac chan da doc duoc 2 bang nay roi vi dang
-- capture chung; GRANT lai la idempotent, khong gay hai.
GRANT SELECT, UPDATE ON kt_bsh.ht_nsd_pvi        TO ogg_bsh;
GRANT SELECT, UPDATE ON kt_bsh.ht_nhom_quyen_pvi TO ogg_bsh;

PROMPT  Da chay 2 lenh GRANT.

PROMPT
PROMPT ===================================================================
PROMPT  4. KIEM CHUNG SAU KHI CAP
PROMPT ===================================================================

SELECT grantee, owner, table_name, privilege, grantable
  FROM dba_tab_privs
 WHERE grantee = 'OGG_BSH'
   AND owner   = 'KT_BSH'
   AND table_name IN ('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI')
 ORDER BY table_name, privilege;

DECLARE
  v_n PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_n
    FROM dba_tab_privs
   WHERE grantee = 'OGG_BSH'
     AND owner   = 'KT_BSH'
     AND privilege = 'UPDATE'
     AND table_name IN ('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI');

  IF v_n <> 2 THEN
    RAISE_APPLICATION_ERROR(-20004,
      'Chi thay ' || v_n || '/2 quyen UPDATE sau khi cap -- KHONG dat.');
  END IF;

  DBMS_OUTPUT.PUT_LINE('  [ OK ] 2/2 quyen UPDATE da cap');
  DBMS_OUTPUT.PUT_LINE('  [ OK ] KHONG cap UPDATE ANY TABLE');
  DBMS_OUTPUT.PUT_LINE(' ');
  DBMS_OUTPUT.PUT_LINE('  BUOC KE TIEP');
  DBMS_OUTPUT.PUT_LINE('    1) chay phep thu : cs9_test_noop_pvi.sql (bang OGG_BSH)');
  DBMS_OUTPUT.PUT_LINE('    2) GO QUYEN NGAY : cs9_revoke_ogg_bsh_src.sql');
END;
/

PROMPT
PROMPT === Xong. Quyen vua cap la TAM THOI, go bang cs9_revoke_ogg_bsh_src.sql ===
PROMPT

-- EXIT la BAT BUOC o cuoi script chay bang `sqlplus @file`: thieu no thi
-- sqlplus chay het script roi CHO INPUT o dau nhac SQL va treo vinh vien.
EXIT
