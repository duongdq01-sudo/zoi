-- =====================================================================
-- cs13_grant_ckpt_ro.sql
--
-- CHAY TREN : DB DICH  BSHC  (172.16.33.90)
-- CHAY BANG : sqlplus / as sysdba
-- CACH CHAY : sqlplus / as sysdba @cs13_grant_ckpt_ro.sql
--
-- MUC DICH
--   Cap SELECT cho GGADM_RO tren MOI bang checkpoint cua Replicat trong
--   schema OGG_BSH. Trang chu doc bang nay de hien "Moc da ap o dich"
--   (seqno/rba/log_csn) - bang chung Replicat apply that chu khong chi
--   hien RUNNING.
--
-- VI SAO CAN
--   Wizard NEW_STREAM / LUONG_LOG chay `add checkpointtable` bang alias
--   OGG_BSH nen bang moi chi OGG_BSH doc duoc. ggadmin khong co sysdba va
--   khong co ket noi OGG_BSH (co y) nen khong tu cap quyen duoc.
--   Thieu quyen thi Oracle bao ORA-00942 (khong phai ORA-01031), trang chu
--   hien loi "khong tim thay bang checkpoint". Da gap that 2026-10-27 voi
--   RPMNV2, RPMNV3, RLOG.
--
-- CHAY LAI BAO NHIEU LAN CUNG DUOC: chi cap cho bang con thieu quyen.
-- Chi cap SELECT (chi doc). Bang phu *_LOX khong can.
-- =====================================================================

SET ECHO         OFF
SET FEEDBACK     OFF
SET VERIFY       OFF
SET PAGESIZE     200
SET LINESIZE     170
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE

PROMPT === 1. Cap SELECT cho GGADM_RO tren bang checkpoint con thieu ===
DECLARE
  n PLS_INTEGER := 0;
BEGIN
  FOR r IN (SELECT c.table_name
              FROM dba_tab_columns c
             WHERE c.owner = 'OGG_BSH'
               AND c.column_name IN ('GROUP_NAME','SEQNO','RBA','LOG_CSN')
               AND c.table_name NOT LIKE '%\_LOX' ESCAPE '\'
               AND c.table_name NOT LIKE 'BIN$%'
             GROUP BY c.table_name
            HAVING COUNT(*) = 4
               AND NOT EXISTS (SELECT 1 FROM dba_tab_privs p
                                WHERE p.owner = 'OGG_BSH'
                                  AND p.table_name = c.table_name
                                  AND p.grantee = 'GGADM_RO'
                                  AND p.privilege = 'SELECT')
             ORDER BY c.table_name) LOOP
    EXECUTE IMMEDIATE 'GRANT SELECT ON OGG_BSH."' || r.table_name || '" TO GGADM_RO';
    DBMS_OUTPUT.PUT_LINE('  da cap SELECT: OGG_BSH.' || r.table_name);
    n := n + 1;
  END LOOP;
  DBMS_OUTPUT.PUT_LINE('  tong: ' || n || ' bang vua cap');
END;
/

PROMPT === 2. Kiem lai: bang checkpoint nao GGADM_RO van chua doc duoc ===
COLUMN table_name FORMAT A30
COLUMN co_quyen   FORMAT A8
SELECT c.table_name,
       CASE WHEN EXISTS (SELECT 1 FROM dba_tab_privs p
                          WHERE p.owner = 'OGG_BSH' AND p.table_name = c.table_name
                            AND p.grantee = 'GGADM_RO' AND p.privilege = 'SELECT')
            THEN 'CO' ELSE 'THIEU' END AS co_quyen
  FROM dba_tab_columns c
 WHERE c.owner = 'OGG_BSH'
   AND c.column_name IN ('GROUP_NAME','SEQNO','RBA','LOG_CSN')
   AND c.table_name NOT LIKE '%\_LOX' ESCAPE '\'
   AND c.table_name NOT LIKE 'BIN$%'
 GROUP BY c.table_name
HAVING COUNT(*) = 4
 ORDER BY c.table_name;

EXIT
