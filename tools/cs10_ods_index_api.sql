-- =====================================================================
-- cs10_ods_index_api.sql
--
-- CHAY TREN : DB DICH BSHC (172.16.33.90)
-- CHAY BANG : sqlplus / as sysdba    (mot lan duy nhat)
-- CACH CHAY : sqlplus / as sysdba @cs10_ods_index_api.sql
--
-- VAN DE NO GIAI QUYET
--   Wizard can `ALTER INDEX ... UNUSABLE/REBUILD`, `GATHER_TABLE_STATS` va
--   `TRUNCATE` tren schema ODS. Ba duong di, hai duong sai:
--
--   SAI 1: dang nhap bang chinh ODS.
--     ODS KHONG CO mot quyen he thong nao (chi role RESOURCE) nen khong
--     dang nhap duoc - `ORA-01045: user ODS lacks CREATE SESSION`. Day la
--     cau hinh DUNG cho mot schema owner giu 3.600 object. Xin cap
--     CREATE SESSION cho no thi cong cu se giu mat khau cua chu schema,
--     tuc la giu kha nang DROP bat ky object nao trong 3.600 object do.
--
--   SAI 2: cap `ALTER ANY INDEX` + `ANALYZE ANY` cho GGADM_LOAD.
--     Quyen "ANY" voi ra ngoai ODS, cham toi moi schema trong DB.
--
--   DUNG: package DEFINER-RIGHTS thuoc ODS, grant EXECUTE cho GGADM_LOAD.
--     GGADM_LOAD co dung nhung gi package cho phep, KHONG co quyen ANY nao
--     va KHONG giu mat khau cua chu schema. Dung tinh than allowlist cua ca
--     du an: cho phep tung Y DINH, khong cho phep tung QUYEN.
--
-- PHAM VI TU GIOI HAN
--   Moi procedure chi lam viec tren bang ma GGADM_LOAD DA co quyen INSERT.
--   Pham vi do la 73 bang dang trong luong GoldenGate. Khong can dong bo
--   danh sach tay: package doc `user_tab_privs_made` nen no tu bam theo
--   dung cac grant da co. Them/bo bang khoi luong la pham vi tu doi theo.
--
-- KHONG CO procedure nao DROP duoc gi.
-- =====================================================================

SET ECHO        OFF
SET FEEDBACK    OFF
SET VERIFY      OFF
SET PAGESIZE    200
SET LINESIZE    170
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE
WHENEVER OSERROR  EXIT FAILURE

COL owner      FORMAT A10
COL object_name FORMAT A18
COL status     FORMAT A8
COL grantee    FORMAT A14
COL privilege  FORMAT A12

PROMPT
PROMPT === 0. CHAN SAI DB (ca hai DB deu ten BSHC, xet theo host_name) ===

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
      'DUNG LAI: host "' || v_host || '" khong phai DB dich 33.90.');
  END IF;
  DBMS_OUTPUT.PUT_LINE('  [ OK ] dung DB dich BSHC');
END;
/

PROMPT
PROMPT === 1. TIEN DIEU KIEN ===

DECLARE
  n PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO n FROM dba_users WHERE username = 'ODS';
  IF n = 0 THEN RAISE_APPLICATION_ERROR(-20002, 'Khong co schema ODS'); END IF;
  SELECT COUNT(*) INTO n FROM dba_users WHERE username = 'GGADM_LOAD';
  IF n = 0 THEN
    RAISE_APPLICATION_ERROR(-20003,
      'Khong co GGADM_LOAD - chay cs6_ggadm_users.sh truoc');
  END IF;
  SELECT COUNT(DISTINCT table_name) INTO n
    FROM dba_tab_privs
   WHERE grantee = 'GGADM_LOAD' AND owner = 'ODS' AND privilege = 'INSERT';
  DBMS_OUTPUT.PUT_LINE('  GGADM_LOAD co quyen INSERT tren ' || n
    || ' bang cua ODS -> day chinh la pham vi cua package');
  IF n = 0 THEN
    RAISE_APPLICATION_ERROR(-20004,
      'GGADM_LOAD khong co quyen INSERT tren bang nao cua ODS. Package se '
      || 'khong lam gi duoc. Chay cs6_ggadm_users.sh truoc.');
  END IF;
END;
/

PROMPT
PROMPT === 2. TAO PACKAGE ODS.GGADM_API ===

CREATE OR REPLACE PACKAGE ODS.GGADM_API AUTHID DEFINER AS
  -- Chay voi quyen cua ODS (DEFINER), nhung CHI tren bang ma GGADM_LOAD
  -- da co quyen INSERT. Khong co procedure nao DROP duoc gi.
  PROCEDURE index_unusable(p_table IN VARCHAR2);
  PROCEDURE index_rebuild (p_table IN VARCHAR2,
                           p_tablespace IN VARCHAR2 DEFAULT 'BSH_INDEX',
                           p_parallel   IN NUMBER   DEFAULT 8);
  PROCEDURE gather_stats  (p_table IN VARCHAR2);
  PROCEDURE truncate_table(p_table IN VARCHAR2);
END GGADM_API;
/

CREATE OR REPLACE PACKAGE BODY ODS.GGADM_API AS

  -- -------------------------------------------------------------------
  -- Pham vi: chi bang ma GGADM_LOAD DA co quyen INSERT.
  -- Doc `user_tab_privs_made` (grant do CHINH ODS cap) nen khong can ODS
  -- co quyen gi them, va pham vi tu bam theo cac grant da co.
  -- -------------------------------------------------------------------
  FUNCTION ten_hop_le(p_table IN VARCHAR2) RETURN VARCHAR2 IS
    v_ten VARCHAR2(128);
    n     PLS_INTEGER;
  BEGIN
    -- DBMS_ASSERT: chan injection o moi cho sinh SQL dong
    v_ten := SYS.DBMS_ASSERT.SIMPLE_SQL_NAME(UPPER(TRIM(p_table)));
    SELECT COUNT(*) INTO n
      FROM user_tab_privs_made
     WHERE grantee = 'GGADM_LOAD'
       AND table_name = v_ten
       AND privilege = 'INSERT';
    IF n = 0 THEN
      RAISE_APPLICATION_ERROR(-20010,
        'Bang ' || v_ten || ' ngoai pham vi: GGADM_LOAD khong co quyen '
        || 'INSERT tren no. Package chi lam viec tren cac bang dang trong '
        || 'luong GoldenGate.');
    END IF;
    RETURN v_ten;
  END ten_hop_le;

  -- -------------------------------------------------------------------
  -- Mark UNUSABLE: CHI index GLOBAL NONUNIQUE.
  -- GIU index UNIQUE o trang thai VALID - chung la thu duy nhat bat duoc
  -- dong trung NGAY trong luc nap. Mark het cho nhanh roi phat hien trung
  -- sau khi da nap 100 trieu dong thi khong con duong lui nao ngoai
  -- TRUNCATE lam lai.
  -- -------------------------------------------------------------------
  PROCEDURE index_unusable(p_table IN VARCHAR2) IS
    v_ten VARCHAR2(128) := ten_hop_le(p_table);
    n     PLS_INTEGER := 0;
  BEGIN
    FOR r IN (SELECT index_name FROM user_indexes
               WHERE table_name = v_ten
                 AND uniqueness = 'NONUNIQUE'
                 AND partitioned = 'NO'
                 AND status = 'VALID'
                 AND index_type IN ('NORMAL', 'FUNCTION-BASED NORMAL'))
    LOOP
      EXECUTE IMMEDIATE 'ALTER INDEX ODS.'
        || SYS.DBMS_ASSERT.ENQUOTE_NAME(r.index_name) || ' UNUSABLE';
      n := n + 1;
      DBMS_OUTPUT.PUT_LINE('  UNUSABLE ' || r.index_name);
    END LOOP;
    DBMS_OUTPUT.PUT_LINE('SO_INDEX=' || n);
  END index_unusable;

  -- -------------------------------------------------------------------
  -- REBUILD: rebuild MOI index dang UNUSABLE cua bang.
  -- Mot index UNUSABLE sot lai se lam moi truy van bo qua no IM LANG
  -- (skip_unusable_indexes = TRUE la mac dinh tu 11g).
  --
  -- `REBUILD ... PARALLEL n` dat DO SONG SONG CO DINH cho index, nen moi
  -- truy van ve sau se lay ke hoach song song. Phai NOPARALLEL ngay sau -
  -- day la cai bay rat de bo qua.
  -- -------------------------------------------------------------------
  PROCEDURE index_rebuild(p_table      IN VARCHAR2,
                          p_tablespace IN VARCHAR2 DEFAULT 'BSH_INDEX',
                          p_parallel   IN NUMBER   DEFAULT 8) IS
    v_ten VARCHAR2(128) := ten_hop_le(p_table);
    v_ts  VARCHAR2(128) := SYS.DBMS_ASSERT.SIMPLE_SQL_NAME(UPPER(TRIM(p_tablespace)));
    v_par PLS_INTEGER   := LEAST(GREATEST(NVL(p_parallel, 8), 1), 16);
    n     PLS_INTEGER := 0;
  BEGIN
    FOR r IN (SELECT index_name FROM user_indexes
               WHERE table_name = v_ten AND status = 'UNUSABLE')
    LOOP
      EXECUTE IMMEDIATE 'ALTER INDEX ODS.'
        || SYS.DBMS_ASSERT.ENQUOTE_NAME(r.index_name)
        || ' REBUILD TABLESPACE ' || v_ts || ' PARALLEL ' || v_par;
      EXECUTE IMMEDIATE 'ALTER INDEX ODS.'
        || SYS.DBMS_ASSERT.ENQUOTE_NAME(r.index_name) || ' NOPARALLEL';
      n := n + 1;
      DBMS_OUTPUT.PUT_LINE('  REBUILD + NOPARALLEL ' || r.index_name);
    END LOOP;
    DBMS_OUTPUT.PUT_LINE('SO_INDEX=' || n);
  END index_rebuild;

  -- `degree => DBMS_STATS.AUTO_DEGREE` chu KHONG phai mot con so co dinh.
  --
  -- Do duoc tren chinh server nay 2026-10-14: `sga_target` 21.056 MB +
  -- `pga_aggregate_limit` 14.000 MB tren may 31 GB RAM = 35 GB tren 31 GB.
  -- Swap 4 GB da DAY 100% va he VAN dang day ra ~9 MB/phut;
  -- `parallel_max_servers` = 320 nen khong co gi chan so slave.
  --
  -- `degree => 4` co dinh nghia la mot bang danh muc 3.355 dong cung lay 4
  -- slave, moi slave mot phan PGA tu cai tran da vuot RAM - khong doi lay
  -- duoc gi. AUTO_DEGREE de Oracle tu quyet theo kich thuoc that.
  PROCEDURE gather_stats(p_table IN VARCHAR2) IS
    v_ten VARCHAR2(128) := ten_hop_le(p_table);
  BEGIN
    DBMS_STATS.GATHER_TABLE_STATS(
      ownname          => 'ODS',
      tabname          => v_ten,
      estimate_percent => DBMS_STATS.AUTO_SAMPLE_SIZE,
      method_opt       => 'FOR ALL COLUMNS SIZE AUTO',
      cascade          => TRUE,
      degree           => DBMS_STATS.AUTO_DEGREE);
    DBMS_OUTPUT.PUT_LINE('  da gather stats ' || v_ten);
    DBMS_OUTPUT.PUT_LINE('SO_INDEX=0');
  END gather_stats;

  -- TRUNCATE khong them pham vi: GGADM_LOAD von da co DELETE tren cac bang
  -- nay. Cai no them la toc do, khong phai tam voi.
  PROCEDURE truncate_table(p_table IN VARCHAR2) IS
    v_ten VARCHAR2(128) := ten_hop_le(p_table);
  BEGIN
    EXECUTE IMMEDIATE 'TRUNCATE TABLE ODS.'
      || SYS.DBMS_ASSERT.ENQUOTE_NAME(v_ten);
    DBMS_OUTPUT.PUT_LINE('  da TRUNCATE ' || v_ten);
    DBMS_OUTPUT.PUT_LINE('SO_INDEX=0');
  END truncate_table;

END GGADM_API;
/

PROMPT
PROMPT === 3. GRANT EXECUTE cho GGADM_LOAD ===

GRANT EXECUTE ON ODS.GGADM_API TO GGADM_LOAD;
PROMPT  da grant EXECUTE ON ODS.GGADM_API TO GGADM_LOAD

PROMPT
PROMPT === 4. KIEM CHUNG ===

SELECT owner, object_name, object_type, status
  FROM dba_objects
 WHERE owner = 'ODS' AND object_name = 'GGADM_API'
 ORDER BY object_type;

SELECT grantee, privilege
  FROM dba_tab_privs
 WHERE owner = 'ODS' AND table_name = 'GGADM_API';

DECLARE
  n       PLS_INTEGER;
  n_pham  PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO n FROM dba_objects
   WHERE owner = 'ODS' AND object_name = 'GGADM_API'
     AND object_type IN ('PACKAGE', 'PACKAGE BODY') AND status = 'VALID';
  IF n <> 2 THEN
    RAISE_APPLICATION_ERROR(-20020,
      'Package/body chua VALID (' || n || '/2). Xem SHOW ERRORS.');
  END IF;
  SELECT COUNT(*) INTO n FROM dba_tab_privs
   WHERE owner = 'ODS' AND table_name = 'GGADM_API'
     AND grantee = 'GGADM_LOAD' AND privilege = 'EXECUTE';
  IF n <> 1 THEN
    RAISE_APPLICATION_ERROR(-20021, 'Chua grant EXECUTE cho GGADM_LOAD');
  END IF;

  -- Dem pham vi THAT thay vi ghi cung mot con so: pham vi tu bam theo cac
  -- grant INSERT dang co, nen no DOI khi them/bo bang khoi luong. Ghi cung
  -- "73 bang" thi thong diep thanh sai ngay lan them bang tiep theo.
  SELECT COUNT(DISTINCT table_name) INTO n_pham
    FROM dba_tab_privs
   WHERE grantee = 'GGADM_LOAD' AND owner = 'ODS' AND privilege = 'INSERT';

  DBMS_OUTPUT.PUT_LINE('  [ OK ] package VALID va da grant EXECUTE');
  DBMS_OUTPUT.PUT_LINE(' ');
  DBMS_OUTPUT.PUT_LINE('  GGADM_LOAD gio lam duoc DUNG 4 viec nay tren '
    || n_pham || ' bang:');
  DBMS_OUTPUT.PUT_LINE('    ODS.GGADM_API.index_unusable(''<bang>'')');
  DBMS_OUTPUT.PUT_LINE('    ODS.GGADM_API.index_rebuild(''<bang>'')');
  DBMS_OUTPUT.PUT_LINE('    ODS.GGADM_API.gather_stats(''<bang>'')');
  DBMS_OUTPUT.PUT_LINE('    ODS.GGADM_API.truncate_table(''<bang>'')');
  DBMS_OUTPUT.PUT_LINE(' ');
  DBMS_OUTPUT.PUT_LINE('  KHONG cap: CREATE SESSION cho ODS, ALTER ANY INDEX,');
  DBMS_OUTPUT.PUT_LINE('  ANALYZE ANY, va khong procedure nao DROP duoc gi.');
END;
/

PROMPT
PROMPT === Xong ===
PROMPT

-- EXIT la BAT BUOC o cuoi moi script chay bang `sqlplus @file`.
-- Thieu no: sqlplus chay het script roi CHO INPUT o dau nhac SQL va
-- treo vinh vien, moi lan chay de lai mot session sysdba treo. Da gap
-- that 2026-10-14: hai session treo sau hai lan chay script nay.
EXIT
