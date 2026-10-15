-- =====================================================================
-- cs11_grant_lo_cn.sql
--
-- CHAY TREN : DB DICH  BSHC  (172.16.33.90)
-- CHAY BANG : sqlplus / as sysdba
-- CACH CHAY : sqlplus / as sysdba @cs11_grant_lo_cn.sql
--
-- MUC DICH
--   Cap quyen cho 8 bang dich cua lo CN_* / XD_ / XL_ truoc khi dua vao
--   luong HT. Tam bang nay DA TON TAI tu truoc (tao 2026-10-09 boi nguoi
--   khac) va chi co GGADM_RO SELECT; GGADM_LOAD KHONG co quyen gi.
--
-- VI SAO CAN CA HAI QUYEN
--   GGADM_LOAD INSERT  -> de NAP du lieu ban dau. Thieu no thi buoc NAP
--                         that bai, va that bai SAU khi da dung Replicat va
--                         da mark index UNUSABLE - tuc la o dung cho dat
--                         nhat trong ca quy trinh.
--   GGADM_RO   SELECT  -> de kiem chung va verify. Thieu no thi bang duoc
--                         nap xong nhung cong cu khong doc duoc de doi
--                         chieu. Day la cam bay so 10 da an that voi
--                         HT_MA_KVUC: 3.355 dong nap xong, buoc kiem chung
--                         FAIL voi ORA-00942.
--
-- QUYEN INSERT CON DINH NGHIA PHAM VI CUA ODS.GGADM_API
--   `ten_hop_le()` trong package doc `user_tab_privs_made` va chi nhan
--   nhung bang ma GGADM_LOAD da co INSERT. Nen cap INSERT o day cung la
--   cach dua 8 bang nay vao pham vi cua package - khong co danh sach tay
--   nao phai dong bo.
--
-- KHONG cap UPDATE / DELETE / DROP. Replicat apply DML bang OGG_BSH (da co
-- quyen rieng), con GGADM_LOAD chi can doc nguon va chen vao dich.
-- =====================================================================

SET ECHO         OFF
SET FEEDBACK     OFF
SET VERIFY       OFF
SET PAGESIZE     200
SET LINESIZE     170
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE
WHENEVER OSERROR  EXIT FAILURE

COL grantee    FORMAT A14
COL table_name FORMAT A16
COL privilege  FORMAT A12

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
PROMPT === 1. TIEN DIEU KIEN: 8 bang phai ton tai, phai RONG, phai thuoc ODS ===

DECLARE
  TYPE tlist IS TABLE OF VARCHAR2(30);
  v_t tlist := tlist('CN_CH', 'CN_CT', 'CN_MA_KH', 'CN_MA_SONO',
                     'CN_PS', 'CN_TT', 'XD_MA_CTR', 'XL_MA_CTR');
  v_n   PLS_INTEGER;
  v_dem PLS_INTEGER;
BEGIN
  FOR i IN 1 .. v_t.COUNT LOOP
    SELECT COUNT(*) INTO v_n
      FROM dba_tables WHERE owner = 'ODS' AND table_name = v_t(i);
    IF v_n = 0 THEN
      RAISE_APPLICATION_ERROR(-20002,
        'Khong co bang ODS.' || v_t(i) || ' -- khong cap quyen gi ca.');
    END IF;

    -- Bang KHONG rong nghia la no dang duoc dung cho viec khac, hoac mot
    -- dot nap truoc da chay. Cap INSERT luc do la mo duong cho mot dot nap
    -- thu hai nhan ban du lieu. Chan o day.
    EXECUTE IMMEDIATE
      'SELECT COUNT(*) FROM (SELECT 1 FROM ods.'
      || DBMS_ASSERT.SIMPLE_SQL_NAME(v_t(i)) || ' WHERE ROWNUM = 1)'
      INTO v_dem;
    IF v_dem > 0 THEN
      RAISE_APPLICATION_ERROR(-20003,
        'Bang ODS.' || v_t(i) || ' KHONG rong. Kiem lai truoc khi cap '
        || 'INSERT: cap xong roi nap vao bang co san la nhan ban du lieu.');
    END IF;

    DBMS_OUTPUT.PUT_LINE('  [ OK ] ODS.' || RPAD(v_t(i), 12) || ' ton tai, rong');
  END LOOP;
END;
/

PROMPT
PROMPT === 2. QUYEN HIEN CO (TRUOC khi cap) ===

SELECT table_name, grantee, privilege
  FROM dba_tab_privs
 WHERE owner = 'ODS'
   AND grantee IN ('GGADM_LOAD', 'GGADM_RO')
   AND table_name IN ('CN_CH', 'CN_CT', 'CN_MA_KH', 'CN_MA_SONO',
                      'CN_PS', 'CN_TT', 'XD_MA_CTR', 'XL_MA_CTR')
 ORDER BY table_name, grantee, privilege;

PROMPT
PROMPT === 3. CAP QUYEN  (idempotent: GRANT lai la vo hai) ===

DECLARE
  TYPE tlist IS TABLE OF VARCHAR2(30);
  v_t tlist := tlist('CN_CH', 'CN_CT', 'CN_MA_KH', 'CN_MA_SONO',
                     'CN_PS', 'CN_TT', 'XD_MA_CTR', 'XL_MA_CTR');
  v_ten VARCHAR2(40);
BEGIN
  FOR i IN 1 .. v_t.COUNT LOOP
    -- DBMS_ASSERT du o day (ten la hang so viet thang trong script, khong
    -- phai dau vao), nhung giu dung mot loi quen nhu trong ODS.GGADM_API.
    v_ten := 'ods.' || DBMS_ASSERT.SIMPLE_SQL_NAME(v_t(i));
    EXECUTE IMMEDIATE 'GRANT INSERT ON ' || v_ten || ' TO ggadm_load';
    EXECUTE IMMEDIATE 'GRANT SELECT ON ' || v_ten || ' TO ggadm_load';
    EXECUTE IMMEDIATE 'GRANT SELECT ON ' || v_ten || ' TO ggadm_ro';
    DBMS_OUTPUT.PUT_LINE('  [ CAP ] ' || RPAD(v_t(i), 12)
      || ' INSERT+SELECT -> GGADM_LOAD, SELECT -> GGADM_RO');
  END LOOP;
END;
/

PROMPT
PROMPT === 4. KIEM CHUNG SAU KHI CAP ===

SELECT table_name, grantee, privilege
  FROM dba_tab_privs
 WHERE owner = 'ODS'
   AND grantee IN ('GGADM_LOAD', 'GGADM_RO')
   AND table_name IN ('CN_CH', 'CN_CT', 'CN_MA_KH', 'CN_MA_SONO',
                      'CN_PS', 'CN_TT', 'XD_MA_CTR', 'XL_MA_CTR')
 ORDER BY table_name, grantee, privilege;

DECLARE
  v_insert PLS_INTEGER;
  v_ro     PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_insert
    FROM dba_tab_privs
   WHERE owner = 'ODS' AND grantee = 'GGADM_LOAD' AND privilege = 'INSERT'
     AND table_name IN ('CN_CH', 'CN_CT', 'CN_MA_KH', 'CN_MA_SONO',
                        'CN_PS', 'CN_TT', 'XD_MA_CTR', 'XL_MA_CTR');

  SELECT COUNT(*) INTO v_ro
    FROM dba_tab_privs
   WHERE owner = 'ODS' AND grantee = 'GGADM_RO' AND privilege = 'SELECT'
     AND table_name IN ('CN_CH', 'CN_CT', 'CN_MA_KH', 'CN_MA_SONO',
                        'CN_PS', 'CN_TT', 'XD_MA_CTR', 'XL_MA_CTR');

  DBMS_OUTPUT.PUT_LINE(' ');
  DBMS_OUTPUT.PUT_LINE('  GGADM_LOAD INSERT : ' || v_insert || '/8');
  DBMS_OUTPUT.PUT_LINE('  GGADM_RO   SELECT : ' || v_ro || '/8');

  IF v_insert <> 8 OR v_ro <> 8 THEN
    RAISE_APPLICATION_ERROR(-20004,
      'Chua du quyen: INSERT ' || v_insert || '/8, SELECT ' || v_ro || '/8.');
  END IF;
  DBMS_OUTPUT.PUT_LINE('  [ OK ] du quyen ca 8 bang');
END;
/

PROMPT
PROMPT === 5. PHAM VI CUA ODS.GGADM_API sau khi cap ===

-- Package tu gioi han pham vi theo cac bang ma GGADM_LOAD co INSERT, nen
-- con so duoi day phai tang len dung 8 bang so voi truoc.
SELECT COUNT(*) AS so_bang_trong_pham_vi_package
  FROM dba_tab_privs
 WHERE owner = 'ODS' AND grantee = 'GGADM_LOAD' AND privilege = 'INSERT';

PROMPT
PROMPT === Xong ===
PROMPT

-- EXIT la BAT BUOC o cuoi script chay bang `sqlplus @file`: thieu no thi
-- sqlplus chay het script roi CHO INPUT o dau nhac SQL va treo vinh vien.
EXIT
