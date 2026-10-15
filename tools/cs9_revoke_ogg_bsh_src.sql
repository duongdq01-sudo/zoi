-- =====================================================================
-- cs9_revoke_ogg_bsh_src.sql
--
-- CHAY TREN : DB NGUON  PMNV  (172.16.33.68)
-- CHAY BANG : sqlplus / as sysdba
-- CACH CHAY : sqlplus / as sysdba @cs9_revoke_ogg_bsh_src.sql
--
-- MUC DICH  : go quyen UPDATE tam thoi da cap o cs9_grant_ogg_bsh_src.sql.
--             CHAY NGAY sau khi chay xong phep thu no-op.
--
-- Chi go UPDATE. KHONG go SELECT, vi OGG_BSH dang capture 2 bang nay va
-- co the dang dua vao quyen doc -- go SELECT la rui ro khong can thiet.
-- =====================================================================

SET ECHO        OFF
SET FEEDBACK    OFF
SET VERIFY      OFF
SET PAGESIZE    200
SET LINESIZE    170
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE
WHENEVER OSERROR  EXIT FAILURE

COL grantee    FORMAT A14
COL owner      FORMAT A10
COL table_name FORMAT A22
COL privilege  FORMAT A12

PROMPT
PROMPT === 0. CHAN SAI DB ===

DECLARE
  v_host VARCHAR2(200);
BEGIN
  SELECT host_name INTO v_host FROM v$instance;
  DBMS_OUTPUT.PUT_LINE('  host_name = ' || v_host);

  IF INSTR(v_host, '33-90') > 0 OR INSTR(v_host, '33.90') > 0 THEN
    RAISE_APPLICATION_ERROR(-20001,
      'DUNG LAI: day la DB DICH. Script nay chi chay o nguon PMNV.');
  END IF;

  IF INSTR(v_host, '33-68') = 0
     AND INSTR(v_host, '33.68') = 0
     AND INSTR(UPPER(v_host), 'PMNV') = 0 THEN
    RAISE_APPLICATION_ERROR(-20001,
      'DUNG LAI: host "' || v_host || '" khong phai DB nguon PMNV.');
  END IF;

  DBMS_OUTPUT.PUT_LINE('  [ OK ] dung DB nguon PMNV');
END;
/

PROMPT
PROMPT === 1. Quyen UPDATE dang co (TRUOC khi go) ===

SELECT grantee, owner, table_name, privilege
  FROM dba_tab_privs
 WHERE grantee = 'OGG_BSH'
   AND owner   = 'KT_BSH'
   AND privilege = 'UPDATE'
   AND table_name IN ('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI')
 ORDER BY table_name;

PROMPT
PROMPT === 2. Go quyen ===

-- Go tung lenh rieng, bat ORA-01927 (khong co quyen de go) de script
-- chay lai lan hai van sach -- idempotent.
DECLARE
  TYPE tlist IS TABLE OF VARCHAR2(30);
  v_t tlist := tlist('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI');
BEGIN
  FOR i IN 1 .. v_t.COUNT LOOP
    BEGIN
      EXECUTE IMMEDIATE
        'REVOKE UPDATE ON kt_bsh.' || v_t(i) || ' FROM ogg_bsh';
      DBMS_OUTPUT.PUT_LINE('  [ GO  ] UPDATE ON KT_BSH.' || v_t(i));
    EXCEPTION
      WHEN OTHERS THEN
        IF SQLCODE = -1927 THEN
          DBMS_OUTPUT.PUT_LINE('  [ BO QUA ] KT_BSH.' || v_t(i)
            || ' chua co quyen UPDATE de go');
        ELSE
          RAISE;
        END IF;
    END;
  END LOOP;
END;
/

PROMPT
PROMPT === 3. Kiem chung: bang duoi day PHAI RONG ===

SELECT grantee, owner, table_name, privilege
  FROM dba_tab_privs
 WHERE grantee = 'OGG_BSH'
   AND owner   = 'KT_BSH'
   AND privilege = 'UPDATE'
   AND table_name IN ('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI')
 ORDER BY table_name;

DECLARE
  v_n PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_n
    FROM dba_tab_privs
   WHERE grantee = 'OGG_BSH'
     AND owner   = 'KT_BSH'
     AND privilege = 'UPDATE'
     AND table_name IN ('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI');

  IF v_n <> 0 THEN
    RAISE_APPLICATION_ERROR(-20005,
      'Van con ' || v_n || ' quyen UPDATE chua go.');
  END IF;

  DBMS_OUTPUT.PUT_LINE('  [ OK ] da go sach quyen UPDATE tam thoi');
END;
/

PROMPT
PROMPT === Xong ===
PROMPT

-- EXIT la BAT BUOC o cuoi script chay bang `sqlplus @file`: thieu no thi
-- sqlplus chay het script roi CHO INPUT o dau nhac SQL va treo vinh vien.
EXIT
