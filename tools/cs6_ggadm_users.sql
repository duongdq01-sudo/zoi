-- =====================================================================
-- cs6_ggadm_users.sql
-- Tao 2 account cho cong cu ggadmin + DB LINK RIENG cho moi account.
--
-- CHAY O DAU : DB DICH (BSHC) tren server 172.16.33.90
-- CHAY BANG  : sqlplus / as sysdba @cs6_ggadm_users.sql
-- CAN SUDO   : khong. User `oracle` co OS authentication.
--
-- MUC DICH: cong cu ggadmin KHONG dung sysdba va KHONG dung OGG_BSH.
--   OGG_BSH hien co o DICH : SELECT/INSERT/UPDATE/DELETE ANY TABLE,
--       CREATE/ALTER/DROP ANY TABLE, ALTER SYSTEM, FLASHBACK ANY TABLE,
--       SELECT ANY DICTIONARY, CONNECT, RESOURCE, SELECT_CATALOG_ROLE
--   OGG_BSH hien co o NGUON: ALTER SYSTEM, ALTER ANY TABLE, SELECT ANY TABLE,
--       LOGMINING, EXEMPT ACCESS POLICY, EXEMPT REDACTION POLICY,
--       DV_GOLDENGATE_ADMIN, DV_XSTREAM_ADMIN, XDBADMIN
--   -> Neu cong cu dung OGG_BSH thi no thua ke toan bo quyen tren.
--
-- TRUOC KHI CHAY: sua 4 bien o muc 0.
-- SAU KHI CHAY  : khai bao mat khau vao /u01/app/ggadmin/conf/secrets.ini
--                 roi XOA file nay hoac chmod 600 (no chua mat khau dang ro).
-- =====================================================================

SET ECHO OFF
SET VERIFY OFF
SET FEEDBACK ON
SET LINESIZE 200
SET PAGESIZE 100
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR CONTINUE

PROMPT
PROMPT ===================================================================
PROMPT === 0. BIEN - SUA 4 DONG DUOI DAY TRUOC KHI CHAY ===
PROMPT ===================================================================

-- Mat khau cho 2 account moi o DB DICH. Dat manh, khong dau nhay don.
DEFINE pw_ro      = 'DoiMatKhauNay_RO_1'
DEFINE pw_load    = 'DoiMatKhauNay_LOAD_1'

-- Credential ma DB LINK dung de dang nhap sang DB NGUON.
-- MAC DINH dung OGG_BSH vi hom nay chi co credential nay.
-- NEN DOI sang mot account chi-doc rieng o nguon ngay khi DBA nguon tao
-- duoc - xem muc 9 cuoi file. Khi do chi can sua 2 dong nay va tao lai link.
DEFINE src_user   = 'OGG_BSH'
DEFINE src_pw     = 'DatMatKhauOGG_BSH_O_NGUON'

-- Chuoi ket noi tai NGUON. Giong y link dang chay OGG_BSH.DBL_PMNV.
-- Dung EZConnect chu KHONG dung alias TNS: ben trong DB, viec phan giai
-- tnsnames.ora phu thuoc TNS_ADMIN cua DB, de hong hon.
-- LUU Y: ca 2 DB deu co SERVICE_NAME = BSHC, nen BAT BUOC phan biet bang IP.
DEFINE src_dsn    = '172.16.33.68:1521/BSHC'

PROMPT
PROMPT ===================================================================
PROMPT === 1. Profile rieng cho account dich vu ===
PROMPT ===================================================================
-- Profile DEFAULT co PASSWORD_LIFE_TIME = 180 ngay. Neu dung DEFAULT thi
-- dung 180 ngay nua cong cu TU DUNG vi mat khau het han. Profile rieng
-- voi UNLIMITED la cach lam chuan cho account dich vu.
-- FAILED_LOGIN_ATTEMPTS giu 10 nhu DEFAULT de van chong do mat khau.

DECLARE
  n PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO n FROM dba_profiles
   WHERE profile = 'GGADMIN_SVC' AND ROWNUM = 1;
  IF n = 0 THEN
    EXECUTE IMMEDIATE q'[CREATE PROFILE GGADMIN_SVC LIMIT
      PASSWORD_LIFE_TIME UNLIMITED
      PASSWORD_GRACE_TIME UNLIMITED
      FAILED_LOGIN_ATTEMPTS 10
      PASSWORD_LOCK_TIME 1]';
    DBMS_OUTPUT.PUT_LINE('  da tao profile GGADMIN_SVC');
  ELSE
    DBMS_OUTPUT.PUT_LINE('  profile GGADMIN_SVC da co, bo qua');
  END IF;
END;
/

PROMPT
PROMPT ===================================================================
PROMPT === 2. Tao 2 account ===
PROMPT ===================================================================

CREATE USER GGADM_RO   IDENTIFIED BY "&pw_ro"
  DEFAULT TABLESPACE BSH_DATA TEMPORARY TABLESPACE TEMP
  PROFILE GGADMIN_SVC
  QUOTA 0 ON BSH_DATA;

CREATE USER GGADM_LOAD IDENTIFIED BY "&pw_load"
  DEFAULT TABLESPACE BSH_DATA TEMPORARY TABLESPACE TEMP
  PROFILE GGADMIN_SVC
  QUOTA 0 ON BSH_DATA;

-- QUOTA 0: ca hai account KHONG tao duoc segment nao cua rieng no.
-- Chung chi doc/ghi vao bang cua schema ODS. Day la mot lop chan them:
-- du bi chiem quyen cung khong the nhoi du lieu lam day tablespace.

PROMPT
PROMPT ===================================================================
PROMPT === 3. Quyen he thong - TOI THIEU, co y khong cap gi them ===
PROMPT ===================================================================

-- GGADM_RO: chi dang nhap + doc tu dien + tao link rieng.
GRANT CREATE SESSION        TO GGADM_RO;
GRANT CREATE DATABASE LINK  TO GGADM_RO;
GRANT SELECT_CATALOG_ROLE   TO GGADM_RO;

-- GGADM_LOAD: them ALTER SESSION de dat parallel/nologging luc nap.
GRANT CREATE SESSION        TO GGADM_LOAD;
GRANT CREATE DATABASE LINK  TO GGADM_LOAD;
GRANT ALTER SESSION         TO GGADM_LOAD;
GRANT SELECT_CATALOG_ROLE   TO GGADM_LOAD;

-- CO Y KHONG CAP, va cong cu khong can:
--   SELECT ANY TABLE      -> cap tung bang o muc 4/5
--   INSERT/UPDATE/DELETE ANY TABLE
--   CREATE/ALTER/DROP ANY TABLE
--   DROP ANY TABLE        -> TRUNCATE se chay bang chinh chu schema ODS,
--                            giong nhu ALTER INDEX REBUILD va GATHER_STATS
--   ALTER SYSTEM, FLASHBACK ANY TABLE, LOGMINING
--   CONNECT / RESOURCE    -> RESOURCE cho tao segment, khong can

PROMPT
PROMPT ===================================================================
PROMPT === 4. GGADM_RO: SELECT tren TOAN BO bang cua ODS (chi doc) ===
PROMPT ===================================================================
-- Cap ca 128 bang ODS chu khong chi 73 bang trong luong: quyen chi-doc
-- khong co rui ro ghi, va dashboard can doc duoc bang ngoai luong de
-- bao "bang ton tai o ODS nhung chua vao GG".

DECLARE
  n PLS_INTEGER := 0;
BEGIN
  FOR r IN (SELECT table_name FROM dba_tables WHERE owner = 'ODS' ORDER BY table_name) LOOP
    EXECUTE IMMEDIATE 'GRANT SELECT ON ODS."' || r.table_name || '" TO GGADM_RO';
    n := n + 1;
  END LOOP;
  DBMS_OUTPUT.PUT_LINE('  GGADM_RO: da cap SELECT tren ' || n || ' bang ODS');
END;
/

PROMPT
PROMPT ===================================================================
PROMPT === 5. GGADM_LOAD: SELECT/INSERT/DELETE CHI tren 73 bang trong luong ===
PROMPT ===================================================================
-- Danh sach lay tu dong MAP ... TARGET ODS.<bang> cua 3 param file
-- (rpmnvods.prm 52 + rht.prm 18 + rdthu.prm 3), doc ngay 2026-10-13.
-- Co y KHONG cap tren 55 bang ODS con lai: chung ngoai pham vi GG,
-- cong cu khong duoc phep cham vao.
-- Neu them bang vao luong thi phai cap them o day.

DECLARE
  TYPE t IS TABLE OF VARCHAR2(128);
  l t := t(
    'BC_BH_HD_DTBH','BC_BH_HD_DTPS','BC_BH_HD_DTTT',
    'BH_2B_GCN','BH_2B_GCN_DK','BH_2B_PHI',
    'BH_BT_HS','BH_BT_HS_DP','BH_BT_HS_NV',
    'BH_CN_API_GCN','BH_DL_MA_KH',
    'BH_HD_DO_CT','BH_HD_DO_TL','BH_HD_DO_TT',
    'BH_HD_GOC','BH_HD_GOC_DKBS','BH_HD_GOC_HH','BH_HD_GOC_HH_CT','BH_HD_MA_KH',
    'BH_HHGCN','BH_HHGCN_DK','BH_HH_MA_HANG','BH_HH_PHI',
    'BH_MA_LDT','BH_MA_LDT_CT','BH_MA_LHNV',
    'BH_NGUOIHD','BH_NGUOIHD_DK','BH_NGUOIHD_DS','BH_NGUOIHD_DSDK',
    'BH_NGUOI_MA_BH','BH_NGUOI_PHI',
    'BH_PHHGCN','BH_PHHGCN_DK','BH_PHHGCN_DVI','BH_PHH_PHI',
    'BH_TAUGCN','BH_TAUGCN_DK','BH_TAUHDGOC','BH_TAULGCN','BH_TAU_MA_TAU','BH_TAU_PHI',
    'BH_XEGCN','BH_XEGCN_DK','BH_XEHDGOC','BH_XELGCN','BH_XE_PHI',
    'HT_MA_CB','HT_MA_DVI','HT_MA_KENH_KT','HT_MA_KENH_TONG','HT_MA_NGUON',
    'HT_MAN_HINH','HT_MA_NSD','HT_MA_NSD_NV','HT_MA_PHONG',
    'HT_NHOM_QUYEN','HT_NHOM_QUYEN_NV','HT_NHOM_QUYEN_PVI',
    'HT_NSD_NHOM_QUYEN','HT_NSD_PVI',
    'KT_1','KT_2','KT_3','KT_BP','KT_MA_TK','KT_MA_TKTKE','KT_SC','KT_SO_SO',
    'TBH_PBO','TT_1','TT_MA_NT','TT_TGTT'
  );
  v_ok PLS_INTEGER := 0;
  v_missing PLS_INTEGER := 0;
  v_n PLS_INTEGER;
BEGIN
  DBMS_OUTPUT.PUT_LINE('  danh sach co ' || l.COUNT || ' bang (mong doi 73)');
  FOR i IN 1 .. l.COUNT LOOP
    -- Kiem bang co that trong tu dien TRUOC khi ghep vao cau GRANT.
    SELECT COUNT(*) INTO v_n FROM dba_tables
     WHERE owner = 'ODS' AND table_name = l(i);
    IF v_n = 1 THEN
      EXECUTE IMMEDIATE 'GRANT SELECT, INSERT, DELETE ON ODS."' || l(i) || '" TO GGADM_LOAD';
      v_ok := v_ok + 1;
    ELSE
      DBMS_OUTPUT.PUT_LINE('  !! KHONG THAY bang ODS.' || l(i) || ' -> bo qua');
      v_missing := v_missing + 1;
    END IF;
  END LOOP;
  DBMS_OUTPUT.PUT_LINE('  GGADM_LOAD: cap duoc ' || v_ok || ' bang, thieu ' || v_missing);
  IF v_missing > 0 THEN
    DBMS_OUTPUT.PUT_LINE('  !! Co bang thieu -> kiem lai truoc khi dung cong cu');
  END IF;
END;
/

PROMPT
PROMPT ===================================================================
PROMPT === 6. DB LINK RIENG cho moi account ===
PROMPT ===================================================================
-- Link la PRIVATE: chi chinh schema so huu dung duoc. Day la ly do phai
-- tao rieng - OGG_BSH.DBL_PMNV la private cua OGG_BSH nen GGADM_* khong
-- dung duoc.
-- Dat CUNG TEN 'DBL_PMNV' trong ca 3 schema. Nho vay moi cau SQL
-- ... @DBL_PMNV trong tai lieu va script cu chay duoc y nguyen,
-- chi khac la ket noi bang account nao.
--
-- Mat khau nguon duoc Oracle luu MA HOA trong tu dien; no KHONG hien ra
-- trong dba_db_links (cot username hien, password thi khong).

-- Khong dung CREATE USER ... IDENTIFIED BY o day duoc, phai tao link
-- TRONG schema do -> dung ALTER SESSION SET CURRENT_SCHEMA khong du,
-- vi CREATE DATABASE LINK luon tao trong schema cua session hien tai.
-- Cach chuan: dang nhap bang chinh account do. Nen muc nay in ra lenh
-- de chay rieng, khong tu chay bang sysdba.

PROMPT
PROMPT   Hai lenh duoi day PHAI chay bang chinh account do, KHONG chay bang sysdba.
PROMPT   Copy ra chay, hoac dung 2 lenh sqlplus o cuoi file nay.
PROMPT
PROMPT   -- bang GGADM_RO:
PROMPT   CREATE DATABASE LINK DBL_PMNV CONNECT TO &src_user
PROMPT     IDENTIFIED BY "<mat khau nguon>" USING '&src_dsn';
PROMPT
PROMPT   -- bang GGADM_LOAD:
PROMPT   CREATE DATABASE LINK DBL_PMNV CONNECT TO &src_user
PROMPT     IDENTIFIED BY "<mat khau nguon>" USING '&src_dsn';
PROMPT

PROMPT
PROMPT ===================================================================
PROMPT === 7. KIEM TRA ===
PROMPT ===================================================================
COL username FOR A14
COL account_status FOR A18
COL profile FOR A14
SELECT username, account_status, profile, default_tablespace,
       TO_CHAR(expiry_date,'YYYY-MM-DD') expiry
  FROM dba_users WHERE username LIKE 'GGADM%' ORDER BY username;

PROMPT
PROMPT --- quyen he thong (phai dung danh sach o muc 3) ---
COL grantee FOR A14
COL privilege FOR A26
SELECT grantee, privilege FROM dba_sys_privs
 WHERE grantee LIKE 'GGADM%' ORDER BY grantee, privilege;

PROMPT
PROMPT --- role ---
SELECT grantee, granted_role FROM dba_role_privs
 WHERE grantee LIKE 'GGADM%' ORDER BY grantee, granted_role;

PROMPT
PROMPT --- so bang duoc cap, theo quyen ---
SELECT grantee, privilege, COUNT(*) so_bang
  FROM dba_tab_privs WHERE grantee LIKE 'GGADM%' AND owner = 'ODS'
 GROUP BY grantee, privilege ORDER BY grantee, privilege;

PROMPT
PROMPT --- khang dinh GGADM_LOAD KHONG ghi duoc ngoai 73 bang ---
SELECT 'GGADM_LOAD co quyen ghi tren ' || COUNT(DISTINCT table_name) ||
       ' / ' || (SELECT COUNT(*) FROM dba_tables WHERE owner='ODS') ||
       ' bang ODS  (mong doi 73)' muc
  FROM dba_tab_privs
 WHERE grantee = 'GGADM_LOAD' AND owner = 'ODS' AND privilege IN ('INSERT','DELETE');

PROMPT
PROMPT --- DB link sau khi tao ---
COL owner FOR A12
COL db_link FOR A14
COL host FOR A34
SELECT owner, db_link, username, host, TO_CHAR(created,'YYYY-MM-DD HH24:MI') created
  FROM dba_db_links WHERE owner IN ('OGG_BSH','GGADM_RO','GGADM_LOAD')
 ORDER BY owner, db_link;

PROMPT
PROMPT ===================================================================
PROMPT === 8. VIEC PHAI LAM SAU KHI CHAY ===
PROMPT ===================================================================
PROMPT   1. Chay 2 lenh CREATE DATABASE LINK o muc 6, bang chinh account do.
PROMPT   2. Them 2 khoi vao /u01/app/ggadmin/conf/secrets.ini (chmod 600):
PROMPT        [ggadm_ro]     user/dsn/password
PROMPT        [ggadm_load]   user/dsn/password
PROMPT   3. XOA file nay hoac chmod 600 - no chua mat khau dang ro.
PROMPT   4. Ghi lai: profile GGADMIN_SVC co PASSWORD_LIFE_TIME UNLIMITED,
PROMPT      nen 2 account nay KHONG het han. Profile DEFAULT la 180 ngay.
PROMPT
PROMPT ===================================================================
PROMPT === 9. NEN LAM TIEP: account chi-doc RIENG o DB NGUON ===
PROMPT ===================================================================
PROMPT   Van de con lai: link dang dung credential OGG_BSH@PMNV, ma OGG_BSH
PROMPT   o nguon co ALTER SYSTEM, SELECT ANY TABLE, LOGMINING, va dac biet
PROMPT   la EXEMPT ACCESS POLICY + EXEMPT REDACTION POLICY. Nghia la cong cu
PROMPT   doc duoc du lieu nguon KHONG bi redact va vuot qua VPD.
PROMPT   Tach account o dich xong nhung phia NGUON thi chua.
PROMPT
PROMPT   De nghi DBA nguon tao, roi sua lai 2 link:
PROMPT
PROMPT     CREATE USER GGADM_SRC_RO IDENTIFIED BY "<...>"
PROMPT       DEFAULT TABLESPACE USERS TEMPORARY TABLESPACE TEMP QUOTA 0 ON USERS;
PROMPT     GRANT CREATE SESSION TO GGADM_SRC_RO;
PROMPT     GRANT SELECT_CATALOG_ROLE TO GGADM_SRC_RO;   -- can cho DBA_* qua link
PROMPT     -- SELECT tung bang trong 73 bang cua KT_BSH, KHONG cap SELECT ANY TABLE:
PROMPT     -- GRANT SELECT ON KT_BSH.<bang> TO GGADM_SRC_RO;   x73
PROMPT     -- KHONG cap: ALTER SYSTEM, LOGMINING, EXEMPT ACCESS/REDACTION POLICY
PROMPT
PROMPT   Luu y: verify dung "SELECT ... AS OF SCN" nen account nguon con can
PROMPT   doc duoc undo trong cua so flashback - quyen SELECT tung bang la du,
PROMPT   KHONG can FLASHBACK ANY TABLE.
PROMPT

SET ECHO OFF
PROMPT === HET ===
