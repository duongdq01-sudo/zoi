-- =====================================================================
-- cs12_dong_bo_quyen_dich.sql
--
-- CHAY TREN : DB DICH  BSHC  (172.16.33.90)
-- CHAY BANG : sqlplus / as sysdba
-- CACH CHAY : sqlplus / as sysdba @cs12_dong_bo_quyen_dich.sql
--
-- MUC DICH
--   Dam bao MOI bang dich dang trong pham vi cong cu co DU 3 quyen:
--       GGADM_LOAD  INSERT   lenh nap
--       GGADM_LOAD  SELECT   LOP CHAN 1 cua script nap (`SELECT 1 FROM
--                            <dich>` de chac bang dang rong) va buoc kiem
--                            chung dem lai so dong
--       GGADM_RO    SELECT   buoc doi chieu nguon / dich
--
-- VI SAO CO SCRIPT NAY
--   Bang dich do NGUOI KHAC tao san (lo 09/10/2026) chi co GGADM_LOAD
--   INSERT va GGADM_RO SELECT - THIEU GGADM_LOAD SELECT. Hau qua: buoc 15
--   cua quy trinh them bang vo voi ORA-01031 NGAY TAI LOP CHAN 1, tuc la
--   sau khi DA dung Replicat. Da xay ra that voi QTN_KT_TAMUNG_LT.
--
-- PHAM VI TU GIOI HAN - day la diem quan trong nhat cua script
--   No chi cham vao bang ma GGADM_LOAD DA CO quyen INSERT. Tap do chinh la
--   cac bang dang trong luong GoldenGate, va cung chinh la pham vi cua goi
--   ODS.GGADM_API. Nho vay KHONG can danh sach ten bang viet tay (danh sach
--   tay se lech ngay lan them bang tiep theo), va script khong the vo tinh
--   mo quyen cho mot bang ngoai pham vi.
--
-- Chi CAP THEM quyen doc. KHONG cap UPDATE / DELETE / DROP, khong go gi.
-- Chay lai nhieu lan vo hai (idempotent).
-- =====================================================================

SET ECHO         OFF
SET FEEDBACK     OFF
SET VERIFY       OFF
SET PAGESIZE     300
SET LINESIZE     170
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE
WHENEVER OSERROR  EXIT FAILURE

COL table_name FORMAT A22
COL thieu      FORMAT A34

PROMPT
PROMPT === 0. CHAN SAI DB  (ca hai DB deu ten BSHC, xet theo host_name) ===

DECLARE
  v_host VARCHAR2(200);
BEGIN
  SELECT host_name INTO v_host FROM v$instance;
  DBMS_OUTPUT.PUT_LINE('  host_name = ' || v_host);

  IF INSTR(v_host, '33-68') > 0 OR INSTR(v_host, '33.68') > 0 THEN
    RAISE_APPLICATION_ERROR(-20001,
      'DUNG LAI: day la DB NGUON PMNV. Script nay chi chay o DB dich 33.90.');
  END IF;

  IF INSTR(v_host, '33-90') = 0 AND INSTR(v_host, '33.90') = 0 THEN
    RAISE_APPLICATION_ERROR(-20001,
      'DUNG LAI: host "' || v_host || '" khong phai DB dich (33.90).');
  END IF;

  DBMS_OUTPUT.PUT_LINE('  [ OK ] dung DB dich BSHC (33.90)');
END;
/

PROMPT
PROMPT === 1. Bang TRONG PHAM VI nhung THIEU quyen (TRUOC khi cap) ===

WITH pham_vi AS (
  SELECT DISTINCT table_name
    FROM dba_tab_privs
   WHERE owner = 'ODS' AND grantee = 'GGADM_LOAD' AND privilege = 'INSERT'
)
SELECT p.table_name,
       RTRIM(
         CASE WHEN NOT EXISTS (
                SELECT 1 FROM dba_tab_privs x
                 WHERE x.owner = 'ODS' AND x.table_name = p.table_name
                   AND x.grantee = 'GGADM_LOAD' AND x.privilege = 'SELECT')
              THEN 'GGADM_LOAD.SELECT ' END ||
         CASE WHEN NOT EXISTS (
                SELECT 1 FROM dba_tab_privs x
                 WHERE x.owner = 'ODS' AND x.table_name = p.table_name
                   AND x.grantee = 'GGADM_RO' AND x.privilege = 'SELECT')
              THEN 'GGADM_RO.SELECT' END) AS thieu
  FROM pham_vi p
 WHERE NOT EXISTS (
         SELECT 1 FROM dba_tab_privs x
          WHERE x.owner = 'ODS' AND x.table_name = p.table_name
            AND x.grantee = 'GGADM_LOAD' AND x.privilege = 'SELECT')
    OR NOT EXISTS (
         SELECT 1 FROM dba_tab_privs x
          WHERE x.owner = 'ODS' AND x.table_name = p.table_name
            AND x.grantee = 'GGADM_RO' AND x.privilege = 'SELECT')
 ORDER BY p.table_name;

PROMPT
PROMPT === 2. CAP THEM quyen doc cho dung nhung bang do ===

DECLARE
  v_n PLS_INTEGER := 0;
BEGIN
  FOR r IN (
    SELECT DISTINCT table_name
      FROM dba_tab_privs
     WHERE owner = 'ODS' AND grantee = 'GGADM_LOAD' AND privilege = 'INSERT'
     ORDER BY table_name
  ) LOOP
    FOR g IN (SELECT 'GGADM_LOAD' AS ai FROM dual
              UNION ALL SELECT 'GGADM_RO' FROM dual) LOOP
      DECLARE
        v_co PLS_INTEGER;
      BEGIN
        SELECT COUNT(*) INTO v_co
          FROM dba_tab_privs
         WHERE owner = 'ODS' AND table_name = r.table_name
           AND grantee = g.ai AND privilege = 'SELECT';
        IF v_co = 0 THEN
          EXECUTE IMMEDIATE 'GRANT SELECT ON ods.'
            || DBMS_ASSERT.SIMPLE_SQL_NAME(r.table_name) || ' TO ' || g.ai;
          v_n := v_n + 1;
          DBMS_OUTPUT.PUT_LINE('  [ CAP ] SELECT ON ODS.'
            || RPAD(r.table_name, 20) || ' -> ' || g.ai);
        END IF;
      END;
    END LOOP;
  END LOOP;

  IF v_n = 0 THEN
    DBMS_OUTPUT.PUT_LINE('  (khong bang nao thieu - khong cap gi)');
  ELSE
    DBMS_OUTPUT.PUT_LINE('  da cap ' || v_n || ' quyen');
  END IF;
END;
/

PROMPT
PROMPT === 3. KIEM CHUNG: bang duoi day PHAI RONG ===

WITH pham_vi AS (
  SELECT DISTINCT table_name
    FROM dba_tab_privs
   WHERE owner = 'ODS' AND grantee = 'GGADM_LOAD' AND privilege = 'INSERT'
)
SELECT p.table_name
  FROM pham_vi p
 WHERE NOT EXISTS (
         SELECT 1 FROM dba_tab_privs x
          WHERE x.owner = 'ODS' AND x.table_name = p.table_name
            AND x.grantee = 'GGADM_LOAD' AND x.privilege = 'SELECT')
    OR NOT EXISTS (
         SELECT 1 FROM dba_tab_privs x
          WHERE x.owner = 'ODS' AND x.table_name = p.table_name
            AND x.grantee = 'GGADM_RO' AND x.privilege = 'SELECT')
 ORDER BY p.table_name;

DECLARE
  v_pham_vi PLS_INTEGER;
  v_thieu   PLS_INTEGER;
BEGIN
  SELECT COUNT(DISTINCT table_name) INTO v_pham_vi
    FROM dba_tab_privs
   WHERE owner = 'ODS' AND grantee = 'GGADM_LOAD' AND privilege = 'INSERT';

  SELECT COUNT(*) INTO v_thieu FROM (
    SELECT DISTINCT t.table_name
      FROM dba_tab_privs t
     WHERE t.owner = 'ODS' AND t.grantee = 'GGADM_LOAD'
       AND t.privilege = 'INSERT'
       AND (NOT EXISTS (
              SELECT 1 FROM dba_tab_privs x
               WHERE x.owner = 'ODS' AND x.table_name = t.table_name
                 AND x.grantee = 'GGADM_LOAD' AND x.privilege = 'SELECT')
            OR NOT EXISTS (
              SELECT 1 FROM dba_tab_privs x
               WHERE x.owner = 'ODS' AND x.table_name = t.table_name
                 AND x.grantee = 'GGADM_RO' AND x.privilege = 'SELECT')));

  DBMS_OUTPUT.PUT_LINE(' ');
  DBMS_OUTPUT.PUT_LINE('  Bang trong pham vi : ' || v_pham_vi);
  DBMS_OUTPUT.PUT_LINE('  Con thieu quyen    : ' || v_thieu);

  IF v_thieu <> 0 THEN
    RAISE_APPLICATION_ERROR(-20006,
      'Van con ' || v_thieu || ' bang thieu quyen doc sau khi cap.');
  END IF;
  DBMS_OUTPUT.PUT_LINE('  [ OK ] moi bang trong pham vi da du 3 quyen');
END;
/

PROMPT
PROMPT === Xong ===
PROMPT

-- EXIT la BAT BUOC o cuoi script chay bang `sqlplus @file`: thieu no thi
-- sqlplus chay het script roi CHO INPUT o dau nhac SQL va treo vinh vien.
EXIT
