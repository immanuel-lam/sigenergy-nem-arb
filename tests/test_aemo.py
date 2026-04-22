"""Tests for AEMO CSV parsing (offline, no network)."""
from __future__ import annotations

from arb.ingest.aemo import _parse_5mpd_csv


# Mimics real AEMO multi-table CSV format
SAMPLE_CSV = """\
C,NEMP.WORLD,P5MIN,AEMO,PUBLIC,2026/04/23,09:05:45
I,P5MIN,CASESOLUTION,2,RUN_DATETIME,STARTINTERVAL_DATETIME,INTERVENTION,TOTALOBJECTIVE
D,P5MIN,CASESOLUTION,2,"2026/04/23 09:10:00","2026/04/23 09:10:00",0,1779260687.80
I,P5MIN,REGIONSOLUTION,10,RUN_DATETIME,INTERVENTION,INTERVAL_DATETIME,REGIONID,RRP,ROP,EXCESSGENERATION
D,P5MIN,REGIONSOLUTION,10,"2026/04/23 09:10:00",0,"2026/04/23 09:10:00",NSW1,85.50,85.50,0
D,P5MIN,REGIONSOLUTION,10,"2026/04/23 09:10:00",0,"2026/04/23 09:15:00",NSW1,92.30,92.30,0
D,P5MIN,REGIONSOLUTION,10,"2026/04/23 09:10:00",0,"2026/04/23 09:10:00",QLD1,55.00,55.00,0
D,P5MIN,REGIONSOLUTION,10,"2026/04/23 09:10:00",1,"2026/04/23 09:10:00",NSW1,90.00,90.00,0
"""


def test_parse_filters_nsw1():
    df = _parse_5mpd_csv(SAMPLE_CSV)
    assert len(df) == 2
    assert all(df["region"] == "NSW1")


def test_parse_converts_rrp():
    df = _parse_5mpd_csv(SAMPLE_CSV)
    # 85.50 $/MWh = 8.55 c/kWh
    assert abs(df["rrp_c_kwh"].iloc[0] - 8.55) < 0.01


def test_parse_filters_intervention():
    """Intervention rows (INTERVENTION=1) should be excluded."""
    df = _parse_5mpd_csv(SAMPLE_CSV)
    # The intervention row for NSW1 at 90.00 should not appear
    assert 9.0 not in df["rrp_c_kwh"].values


def test_parse_empty_input():
    df = _parse_5mpd_csv("")
    assert df.empty
