"""Test khung "Doi chieu cau hinh" (app/collector.Collector.param_mismatch).
Khong can Oracle/ggsci.

Loi da gap 2026-10-30: dashboard so voi HANG SO viet tay (etht 18, TRANDATA
73) nen them bang qua quy trinh xong la bao lech vinh vien, du compare khop.
Nay so TUNG CAP extract/replicat voi nhau."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.collector import Collector                           # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


STREAMS = {"HT": {"extract": "ETHT", "replicat": "RHT"},
           "PMNV3": {"extract": "ETPMNV3", "replicat": "RPMNV3"}}


def _lech(counts, prepared=350, credstore=True):
    c = Collector(SimpleNamespace(streams=STREAMS), con=None, ora=None)
    c.snap.param_counts = counts
    c.snap.prepared = prepared
    c.snap.credstore = credstore
    return c.param_mismatch()


@test
def them_bang_ca_hai_dau_khong_bao_lech():
    # HT 18 -> 29 bang, prepared 73 -> 350: deu la binh thuong
    assert _lech({"HT": (29, 29), "PMNV3": (238, 238)}) == []


@test
def extract_va_replicat_lech_nhau_thi_bao():
    out = _lech({"HT": (30, 29), "PMNV3": (238, 238)})
    assert len(out) == 1 and "etht.prm có 30" in out[0] and "rht.prm có 29" in out[0]


@test
def thieu_file_prm_thi_bao_ten_file():
    out = _lech({"HT": (29, -1)})
    assert out == ["luồng HT: không đọc được rht.prm"]


@test
def credstore_mat_van_bao():
    assert any("cwallet.sso" in m for m in _lech({}, credstore=False))
