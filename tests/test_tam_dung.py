"""Test co `tam_dung` cua [streams.X] (app/collector.process_tam_dung).
Khong can Oracle/ggsci."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.collector import process_tam_dung                    # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _cfg(**streams):
    return SimpleNamespace(streams=streams)


@test
def luong_tam_dung_bo_qua_ca_extract_va_replicat():
    c = _cfg(PM={"extract": "ETPMNV", "replicat": "RPMNVODS"},
             DT={"extract": "ETDTHU", "replicat": "RDTHU", "tam_dung": True})
    assert process_tam_dung(c) == {"ETDTHU", "RDTHU"}


@test
def khong_co_co_hoac_co_false_thi_van_canh_bao():
    c = _cfg(DT={"extract": "ETDTHU", "replicat": "RDTHU", "tam_dung": False},
             HT={"extract": "ETHT", "replicat": "RHT"})
    assert process_tam_dung(c) == set()


@test
def chuoi_true_khong_tinh_la_tam_dung():
    # toml viet nham `tam_dung = "true"` -> KHONG tat canh bao (an toan ve phia bao).
    c = _cfg(DT={"extract": "ETDTHU", "replicat": "RDTHU", "tam_dung": "true"})
    assert process_tam_dung(c) == set()


@test
def khong_co_streams():
    assert process_tam_dung(SimpleNamespace(streams=None)) == set()
