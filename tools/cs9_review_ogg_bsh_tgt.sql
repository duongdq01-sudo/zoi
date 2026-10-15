-- =====================================================================
-- cs9_review_ogg_bsh_tgt.sql
--
-- CHAY TREN : DB DICH  BSHC  (172.16.33.90)
-- CHAY BANG : sqlplus / as sysdba
-- CACH CHAY : sqlplus / as sysdba @cs9_review_ogg_bsh_tgt.sql
--
-- SCRIPT NAY CHI DOC. Khong GRANT, khong REVOKE, khong doi gi.
-- No RA SOAT quyen cua OGG_BSH va SINH RA lenh REVOKE de nguoi doc tu
-- quyet, dung mo hinh da dung cho streams_pool_size: cong cu kiem tra va
-- sinh lenh, nguoi chay lenh.
--
-- VI SAO KHONG TU CHAY REVOKE
--   OGG_BSH la account dang chay Replicat production. Mot phan quyen cua
--   no do DBMS_GOLDENGATE_AUTH.GRANT_ADMIN_PRIVILEGE cap thanh MOT BO;
--   go le tung quyen trong bo do co the lam Replicat chet o lan restart
--   SAU DO, chu khong chet ngay -- loai loi kho truy nhat.
--   Vi vay: xem bang "nguon cap" ben duoi truoc khi go bat cu thu gi.
--
-- VI SAO KHONG CAP ANALYZE ANY CHO OGG_BSH
--   Da can nhac va BO. Ly do: gather statistics o ODS chay bang CHINH CHU
--   SCHEMA ODS (chu schema luon lam duoc tren object cua minh), dung nhu
--   quyet dinh kien truc cua ggadmin. Cap ANALYZE ANY cho OGG_BSH la noi
--   quyen di nguoc huong dang don.
-- =====================================================================

SET ECHO        OFF
SET FEEDBACK    OFF
SET VERIFY      OFF
SET PAGESIZE    500
SET LINESIZE    170
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE

COL privilege  FORMAT A34
COL admin_option FORMAT A6
COL granted_role FORMAT A28
COL danh_gia   FORMAT A46
COL lenh       FORMAT A74
COL owner      FORMAT A12
COL table_name FORMAT A26

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
    DBMS_OUTPUT.PUT_LINE('  [ CANH BAO ] host khong khop 33.90 --'
      || ' chi doc nen khong chan, nhung hay kiem lai dang o dau');
  ELSE
    DBMS_OUTPUT.PUT_LINE('  [ OK ] dung DB dich BSHC (33.90)');
  END IF;
END;
/

PROMPT
PROMPT ===================================================================
PROMPT  1. QUYEN HE THONG CAP TRUC TIEP CHO OGG_BSH + danh gia
PROMPT ===================================================================

SELECT p.privilege,
       p.admin_option,
       CASE p.privilege
         WHEN 'ALTER SYSTEM'       THEN 'KHONG can cho Integrated Replicat -> nen go'
         WHEN 'DROP ANY TABLE'     THEN 'KHONG can -> nen go (rui ro cao nhat)'
         WHEN 'DROP ANY EDITION'   THEN 'KHONG can -> nen go'
         WHEN 'INSERT ANY TABLE'   THEN 'GIU: Replicat apply DML'
         WHEN 'UPDATE ANY TABLE'   THEN 'GIU: Replicat apply DML'
         WHEN 'DELETE ANY TABLE'   THEN 'GIU: Replicat apply DML'
         WHEN 'SELECT ANY TABLE'   THEN 'GIU: doc truoc khi apply'
         WHEN 'FLASHBACK ANY TABLE' THEN 'GIU: can cho verify/doi chieu'
         WHEN 'CREATE DATABASE LINK' THEN 'GIU: OGG_BSH dang so huu DBL_PMNV'
         ELSE '-'
       END AS danh_gia
  FROM dba_sys_privs p
 WHERE p.grantee = 'OGG_BSH'
 ORDER BY CASE WHEN p.privilege IN
            ('ALTER SYSTEM', 'DROP ANY TABLE', 'DROP ANY EDITION')
          THEN 0 ELSE 1 END,
          p.privilege;

PROMPT
PROMPT ===================================================================
PROMPT  2. ROLE cua OGG_BSH  (quyen qua role khong go bang REVOKE truc tiep)
PROMPT ===================================================================

SELECT granted_role, admin_option, default_role
  FROM dba_role_privs
 WHERE grantee = 'OGG_BSH'
 ORDER BY granted_role;

PROMPT
PROMPT  Ba quyen de xuat go CO den tu role nao khong?
PROMPT  Co dong o day nghia la REVOKE truc tiep se KHONG lam mat quyen do:

-- Phai dung DBA_SYS_PRIVS voi grantee = ten ROLE. KHONG dung ROLE_SYS_PRIVS:
-- view do chi hien role cua SESSION HIEN TAI (tuc la SYS), khong phai cua
-- OGG_BSH -- se cho ket qua sai ma nhin khong ra.
SELECT r.granted_role, sp.privilege
  FROM dba_role_privs r
  JOIN dba_sys_privs sp ON sp.grantee = r.granted_role
 WHERE r.grantee = 'OGG_BSH'
   AND sp.privilege IN ('ALTER SYSTEM', 'DROP ANY TABLE', 'DROP ANY EDITION')
 ORDER BY r.granted_role, sp.privilege;

PROMPT
PROMPT ===================================================================
PROMPT  3. LENH REVOKE DUOC SINH RA  -- script nay KHONG chay chung
PROMPT ===================================================================

PROMPT
PROMPT  Copy nhung dong duoi day va chay tay NEU dong y:

SELECT 'REVOKE ' || privilege || ' FROM ogg_bsh;' AS lenh
  FROM dba_sys_privs
 WHERE grantee = 'OGG_BSH'
   AND privilege IN ('ALTER SYSTEM', 'DROP ANY TABLE', 'DROP ANY EDITION')
 ORDER BY privilege;

DECLARE
  v_n PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_n
    FROM dba_sys_privs
   WHERE grantee = 'OGG_BSH'
     AND privilege IN ('ALTER SYSTEM', 'DROP ANY TABLE', 'DROP ANY EDITION');

  DBMS_OUTPUT.PUT_LINE(' ');
  IF v_n = 0 THEN
    DBMS_OUTPUT.PUT_LINE('  [ OK ] khong con quyen nao trong 3 quyen de xuat go.');
  ELSE
    DBMS_OUTPUT.PUT_LINE('  ' || v_n || '/3 quyen de xuat go dang con.');
    DBMS_OUTPUT.PUT_LINE(' ');
    DBMS_OUTPUT.PUT_LINE('  TRUOC KHI GO, doc ky:');
    DBMS_OUTPUT.PUT_LINE('   - Go ALTER SYSTEM thi OGG_BSH khong con tu doi');
    DBMS_OUTPUT.PUT_LINE('     duoc tham so DB. Integrated Replicat khong can,');
    DBMS_OUTPUT.PUT_LINE('     nhung neu co script van hanh nao dang dua vao thi');
    DBMS_OUTPUT.PUT_LINE('     script do se chet.');
    DBMS_OUTPUT.PUT_LINE('   - Go xong PHAI thu restart 1 Replicat ngoai gio de');
    DBMS_OUTPUT.PUT_LINE('     chac chan: loi thieu quyen thuong chi lo ra o lan');
    DBMS_OUTPUT.PUT_LINE('     restart, khong lo ra ngay.');
    DBMS_OUTPUT.PUT_LINE('   - Co cach go roi cap lai bang bo chuan:');
    DBMS_OUTPUT.PUT_LINE('     DBMS_GOLDENGATE_AUTH.GRANT_ADMIN_PRIVILEGE(');
    DBMS_OUTPUT.PUT_LINE('       grantee => ''OGG_BSH'', privilege_type => ''APPLY'');');
  END IF;
END;
/

PROMPT
PROMPT ===================================================================
PROMPT  4. Quyen object cua OGG_BSH tren schema ODS  (de biet pham vi that)
PROMPT ===================================================================

SELECT owner, privilege, COUNT(*) AS so_bang
  FROM dba_tab_privs
 WHERE grantee = 'OGG_BSH'
 GROUP BY owner, privilege
 ORDER BY owner, privilege;

PROMPT
PROMPT === Xong. Script nay khong doi gi -- moi lenh REVOKE o muc 3 la de nguoi chay ===
PROMPT

-- EXIT la BAT BUOC o cuoi script chay bang `sqlplus @file`: thieu no thi
-- sqlplus chay het script roi CHO INPUT o dau nhac SQL va treo vinh vien.
EXIT
