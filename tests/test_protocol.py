import math

import pytest

from metex_dmm.protocol import (FrameSplitter, estimate_range, format_number,
                                parse_frame, parse_si, parse_unit)
from metex_dmm.simulator import make_frame


# --- skutečné a dokumentované rámce ----------------------------------------

def test_real_frame_20v_range():
    # skutečně přijato z přístroje 22. 11. 2026 (rozsah 20 V, hroty naprázdno)
    r = parse_frame(b"DC  00.00   V\r")
    assert r is not None
    assert (r.mode, r.value, r.unit.raw, r.unit.base) == ("DC", 0.0, "V", "V")
    assert r.range_value == 20
    assert r.range_label == "20 V"
    assert not r.overload


def test_manual_example_1():
    # návod str. 36, příklad 1: "DC -3.999 V<CR>" (14 znaků)
    r = parse_frame(make_frame("DC", "-3.999", "V"))
    assert r.value == -3.999 and r.unit.raw == "V"


def test_manual_example_2_megaohm():
    # návod str. 36, příklad 2: "OH  3.999MOhm<CR>"
    frame = b"OH  3.999MOhm\r"
    assert len(frame) == 14
    r = parse_frame(frame)
    assert r.mode == "OH" and r.mode_label == "Ω"
    assert r.value == 3.999
    assert r.unit.label == "MΩ" and r.unit.base == "Ω"
    assert r.value_si == pytest.approx(3.999e6)


@pytest.mark.parametrize("frame,value,unit,si", [
    (b"DC  001.0  mA\r", 1.0, "mA", 1e-3),
    (b"DC  01.01  mA\r", 1.01, "mA", 1.01e-3),
    (b"DC -0.001  mA\r", -0.001, "mA", -1e-6),
    (b"DC  000.5  uA\r", 0.5, "uA", 0.5e-6),
    (b"AC  123.4  mV\r", 123.4, "mV", 0.1234),
    (b"OH  1.234 KOhm\r"[:13] + b"\r", None, None, None),  # useknuté -> None
    (b"CA  0.123   nF\r"[:14], None, None, None),
    (make_frame("FR", "1.000", "KHz"), 1.0, "KHz", 1000.0),
    (make_frame("FR", "1.000", "MHz"), 1.0, "MHz", 1e6),
    (make_frame("FR", "123.4", "Hz"), 123.4, "Hz", 123.4),
    (make_frame("CA", "12.3", "nF"), 12.3, "nF", 12.3e-9),
    (make_frame("CA", "1.00", "pF"), 1.0, "pF", 1e-12),
    (b"DC -00.08   V\r", -0.08, "V", -0.08),   # vektor z QtDMM (ME-22T)
    (b"FR  0.050 KHz\r", 0.05, "KHz", 50.0),    # vektor z QtDMM
])
def test_values(frame, value, unit, si):
    r = parse_frame(frame)
    if value is None:
        # rámce, které nemají 14 znaků s CR na konci, musí být odmítnuty
        assert r is None or len(frame) == 14
        return
    assert r.value == pytest.approx(value)
    assert r.unit.raw == unit
    assert r.value_si == pytest.approx(si)


@pytest.mark.parametrize("val", ["O.L", " O.L", "-O.L", ".OL", "0.L", "OL."])
def test_overload(val):
    r = parse_frame(make_frame("DC", val, "mA"))
    assert r is not None and r.overload and r.value is None
    assert r.value_si is None


@pytest.mark.parametrize("frame", [
    b"",                      # nic
    b"DC  00.00   V",         # bez CR
    b"DC  00.00   V\n",       # LF místo CR
    b"C  00.00   V\r",        # 13 znaků
    b"DC  00.00    V\r",      # 15 znaků
    b"DC  00\x0000   V\r",    # netisknutelný znak
    b"12  00.00   V\r",       # režim není z písmen
])
def test_invalid_frames(frame):
    assert parse_frame(frame) is None


def test_text_value_is_kept():
    # např. logická sonda nebo neznámý text: struktura sedí, číslo ne
    r = parse_frame(make_frame("LO", "Hi", ""))
    assert r is not None and r.value is None and not r.overload
    assert r.text == "Hi"


def test_empty_mode_is_accepted():
    # sigrok: dvě mezery místo režimu = režim podle jednotky
    r = parse_frame(b"    00.00   V\r")
    assert r is not None and r.mode == "" and r.value == 0.0


@pytest.mark.parametrize("frame", [b"OH   OL.  Ohm\r", b"DI    OL   mV\r"])
def test_qtdmm_overload_vectors(frame):
    r = parse_frame(frame)
    assert r.overload and r.unit.raw in ("Ohm", "mV")


def test_unknown_mode_passes():
    r = parse_frame(make_frame("XX", "1.000", "V"))
    assert r.mode == "XX" and r.mode_label == "XX"


def test_parity_bit_is_masked():
    frame = bytes(b | 0x80 for b in b"DC  12.34   V") + b"\r"
    r = parse_frame(frame)
    assert r is not None and r.value == 12.34


@pytest.mark.parametrize("text,mode,unit,rng", [
    ("0.000", "DC", "V", 2), ("00.00", "DC", "V", 20), ("000.0", "DC", "V", 200),
    ("0000", "DC", "V", 1000), ("000", "AC", "V", 750), ("199.9", "DC", "mV", 200),
    ("-1.999", "DC", "V", 2), ("000.0", "DC", "uA", 200), ("00.00", "DC", "A", 20),
    ("abc", "DC", "V", None),
])
def test_estimate_range(text, mode, unit, rng):
    assert estimate_range(text, mode, unit) == rng


def test_units():
    assert parse_unit("uA").label == "µA"
    assert parse_unit("KOhm").factor == 1e3
    assert parse_unit("MOhm").factor == 1e6
    assert parse_unit("mV").factor == 1e-3
    assert parse_unit("").base == ""


# --- dělení proudu bajtů na rámce ------------------------------------------

def test_splitter_joins_chunks():
    s = FrameSplitter()
    f = b"DC  12.34   V\r"
    assert s.feed(f[:5]) == []
    assert s.feed(f[5:]) == [f]


def test_splitter_many_and_garbage():
    s = FrameSplitter()
    f1 = b"DC  12.34   V\r"
    f2 = b"DC -00.01   V\r"
    out = s.feed(b"\x7f\x00xx" + f1 + b"zz\r" + f2)
    frames = [parse_frame(c) for c in out]
    assert [r.value for r in frames if r] == [12.34, -0.01]
    assert s.resyncs == 1 and s.fragments == 1


def test_splitter_noise_without_cr_is_bounded():
    s = FrameSplitter()
    s.feed(b"x" * 10_000)
    assert len(s.buf) <= FrameSplitter.MAX_BUF


# --- pomocné funkce ----------------------------------------------------------

@pytest.mark.parametrize("text,val", [
    ("15m", 0.015), ("1,2 k", 1200.0), ("0.015", 0.015), ("15mA", 0.015),
    ("2MOhm", 2e6), ("-3", -3.0), ("", None), ("abc", None), ("100uA", 1e-4),
])
def test_parse_si(text, val):
    got = parse_si(text)
    assert (got is None and val is None) or got == pytest.approx(val)


def test_format_number():
    assert format_number(math.nan) == "—"
    assert format_number(12.3456) == "12.35"
    assert format_number(0.0012345) == "0.001234" or format_number(0.0012345) == "0.001235"
