# SPDX-License-Identifier: GPL-3.0-or-later
import csv
import math

import pytest

from metex_dmm.csvlog import COLUMNS, CsvLogger
from metex_dmm.protocol import parse_frame
from metex_dmm.simulator import make_frame
from metex_dmm.stats import RunStats, WindowStats


def test_run_stats():
    s = RunStats()
    assert math.isnan(s.summary().mean)
    for v in (1.0, 2.0, 3.0, 4.0):
        s.add(v)
    sm = s.summary()
    assert (sm.n, sm.mean, sm.min, sm.max) == (4, 2.5, 1.0, 4.0)
    assert sm.std == pytest.approx(1.2909944)


def test_window_stats_drops_old():
    w = WindowStats(10)
    for t in range(0, 30):
        w.add(float(t), float(t))
    sm = w.summary()
    assert sm.n == 11 and sm.min == 19 and sm.max == 29
    w.add_overload(29.5)
    assert w.overload_count == 1
    assert w.summary(now=100).n == 0


def _r(val, unit="mA", t=1_700_000_000.0):
    r = parse_frame(make_frame("DC", val, unit))
    r.t_wall = t
    return r


def test_csv_excel_cz(tmp_path):
    p = tmp_path / "a.csv"
    log = CsvLogger(str(p), excel_cz=True)
    log.write(_r("12.34"))
    log.write(_r("O.L", t=1_700_000_000.5))
    log.close()
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")          # BOM pro Excel
    rows = list(csv.reader(p.read_text("utf-8-sig").splitlines(), delimiter=";"))
    assert rows[0] == COLUMNS
    assert rows[1][3] == "12,34" and rows[1][5] == "0,01234"
    assert rows[2][3] == "" and rows[2][8] == "1" and rows[2][1] == "0,500"


def test_csv_standard_append(tmp_path):
    p = tmp_path / "b.csv"
    for _ in range(2):
        log = CsvLogger(str(p), excel_cz=False)
        log.write(_r("-0.001", "V"))
        log.close()
    rows = list(csv.reader(p.read_text("utf-8").splitlines()))
    assert len(rows) == 3                            # hlavička jen jednou
    assert rows[1][3] == "-0.001" and rows[1][6] == "V"
