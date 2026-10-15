-- =====================================================================
-- cs9_test_noop_pvi.sql
--
-- CHAY TREN : DB NGUON  PMNV  (172.16.33.68)
-- CHAY BANG : chinh OGG_BSH  (KHONG phai sysdba)
-- CACH CHAY : sqlplus OGG_BSH/<pw>@PMNV @cs9_test_noop_pvi.sql
--             hoac goi qua  bash cs9_grant_ogg_bsh.sh --test
--
-- MUC DICH
--   Chot cau hoi con mo cua lo HT: 2 bang *_PVI khong co PK that, dung
--   KEYCOLS, ma cot PHONG co gia tri NULL. Replicat dung KEYCOLS dung
--   cau WHERE -- neu mot cot khoa la NULL thi "PHONG = NULL" KHONG BAO GIO
--   dung, Replicat khong tim thay dong, sinh ORA-01403.
--   Param dang co REPERROR (1403, DISCARD) nen bang se bi BO IM LANG:
--   khong abend, ggserr.log sach, chi discard file to len.
--
-- THIET KE PHEP THU -- hai ca, de ket luan co doi chung:
--   ca A: dong co PHONG IS NULL      -> KY VONG discard file to len
--   ca B: dong co PHONG IS NOT NULL  -> KY VONG apply binh thuong
--   Chi ca A vao discard thi gia thuyet DUNG. Ca B cung vao discard thi
--   van de nam o cho khac (khong phai NULL), phai dieu tra lai.
--
-- AN TOAN
--   Lenh dung la  SET PHONG = PHONG  tren DUNG MOT dong, xac dinh bang
--   ROWID. Day la no-op that: khong doi mot byte du lieu nao, ke ca khi
--   PHONG IS NULL (NULL gan vao NULL). Nhung redo VAN sinh ra, nen GG van
--   co ban ghi de replicate -- do la toan bo muc dich.
--   Ten bang trong script la hang so viet thang, khong phai dau vao cua
--   nguoi dung, nen dynamic SQL o day khong co be mat injection.
--
-- SAU KHI CHAY: kiem o server GG 172.16.33.90
--   stat -c%s /u01/app/ogg/dirrpt/RHT.dsc     # moc rong la 113 byte
--   tail -40 /u01/app/ogg/dirrpt/RHT.dsc
-- =====================================================================

SET ECHO        OFF
SET FEEDBACK    OFF
SET VERIFY      OFF
SET PAGESIZE    200
SET LINESIZE    170
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE

PROMPT
PROMPT ===================================================================
PROMPT  PHEP THU NO-OP UPDATE tren 2 bang *_PVI
PROMPT ===================================================================

DECLARE
  TYPE tlist IS TABLE OF VARCHAR2(30);
  v_t      tlist := tlist('HT_NSD_PVI', 'HT_NHOM_QUYEN_PVI');
  v_n      PLS_INTEGER;
  v_rid    VARCHAR2(30);
  v_scn    NUMBER;
  v_done_a PLS_INTEGER := 0;
  v_done_b PLS_INTEGER := 0;

  -- Lay 1 ROWID theo dieu kien PHONG NULL / NOT NULL. Tra NULL neu khong co.
  FUNCTION lay_rowid(p_tab VARCHAR2, p_null BOOLEAN) RETURN VARCHAR2 IS
    v_r VARCHAR2(30);
  BEGIN
    EXECUTE IMMEDIATE
      'SELECT ROWIDTOCHAR(ROWID) FROM kt_bsh.' || p_tab
      || ' WHERE phong IS ' || CASE WHEN p_null THEN 'NULL' ELSE 'NOT NULL' END
      || ' AND ROWNUM = 1'
      INTO v_r;
    RETURN v_r;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN RETURN NULL;
  END;

  -- No-op UPDATE dung 1 dong, xac dinh bang ROWID.
  PROCEDURE noop(p_tab VARCHAR2, p_rid VARCHAR2, p_nhan VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE
      'UPDATE kt_bsh.' || p_tab
      || ' SET phong = phong WHERE ROWID = CHARTOROWID(:1)'
      USING p_rid;
    DBMS_OUTPUT.PUT_LINE('    ' || p_nhan || ' rowid=' || p_rid
      || '  -> ' || SQL%ROWCOUNT || ' dong');
  END;

BEGIN
  FOR i IN 1 .. v_t.COUNT LOOP
    DBMS_OUTPUT.PUT_LINE(' ');
    DBMS_OUTPUT.PUT_LINE('  KT_BSH.' || v_t(i));

    SELECT COUNT(*) INTO v_n
      FROM all_tab_columns
     WHERE owner = 'KT_BSH' AND table_name = v_t(i) AND column_name = 'PHONG';
    IF v_n = 0 THEN
      DBMS_OUTPUT.PUT_LINE('    [BO QUA] khong co cot PHONG');
      CONTINUE;
    END IF;

    -- ca A: PHONG IS NULL
    v_rid := lay_rowid(v_t(i), TRUE);
    IF v_rid IS NULL THEN
      DBMS_OUTPUT.PUT_LINE('    [ca A] khong co dong nao PHONG IS NULL'
        || ' -- bang nay khong tai hien duoc van de');
    ELSE
      noop(v_t(i), v_rid, '[ca A] PHONG IS NULL    ');
      v_done_a := v_done_a + 1;
    END IF;

    -- ca B: doi chung
    v_rid := lay_rowid(v_t(i), FALSE);
    IF v_rid IS NULL THEN
      DBMS_OUTPUT.PUT_LINE('    [ca B] khong co dong nao PHONG NOT NULL');
    ELSE
      noop(v_t(i), v_rid, '[ca B] PHONG NOT NULL  ');
      v_done_b := v_done_b + 1;
    END IF;
  END LOOP;

  COMMIT;

  DBMS_OUTPUT.PUT_LINE(' ');
  DBMS_OUTPUT.PUT_LINE('  Da COMMIT: ' || v_done_a || ' ca A, '
    || v_done_b || ' ca B.');

  -- SCN de doi chieu voi trail / discard. Khong phai account nao cung doc
  -- duoc v$database nen boc rieng, that bai thi khong lam do phep thu.
  BEGIN
    SELECT current_scn INTO v_scn FROM v$database;
    DBMS_OUTPUT.PUT_LINE('  SCN sau COMMIT : ' || v_scn);
  EXCEPTION
    WHEN OTHERS THEN
      DBMS_OUTPUT.PUT_LINE('  (khong doc duoc v$database.current_scn: '
        || SQLCODE || ')');
  END;

  DBMS_OUTPUT.PUT_LINE('  Thoi diem      : '
    || TO_CHAR(SYSTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'));
  DBMS_OUTPUT.PUT_LINE(' ');
  DBMS_OUTPUT.PUT_LINE('  DOC KET QUA o server GG 172.16.33.90:');
  DBMS_OUTPUT.PUT_LINE('    stat -c%s /u01/app/ogg/dirrpt/RHT.dsc   (moc rong 113)');
  DBMS_OUTPUT.PUT_LINE('    113 byte  -> khong co gi bi discard');
  DBMS_OUTPUT.PUT_LINE('    > 113     -> tail -40 RHT.dsc xem bang nao,'
    || ' ORA-01403 hay khong');
END;
/

PROMPT
PROMPT === Xong. NHO go quyen: cs9_revoke_ogg_bsh_src.sql ===
PROMPT

-- EXIT la BAT BUOC o cuoi script chay bang `sqlplus @file`: thieu no thi
-- sqlplus chay het script roi CHO INPUT o dau nhac SQL va treo vinh vien.
EXIT
