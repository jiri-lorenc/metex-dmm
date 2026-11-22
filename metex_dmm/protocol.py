"""Protokol multimetru Metex M-3650D (řada M-3600D), formát „metex14“.

Linka: 1200 Bd, 7 datových bitů, bez parity, 2 stop bity. DTR=1 a RTS=0
napájejí optočlen v kabelu MT/RS232C. Na dotaz ASCII ``D`` (pasivní režim)
nebo samovolně (aktivní režim, na displeji „COM“; ověřeno ~2,5×/s)
přístroj pošle 14 znaků ASCII::

    znak (od 1)  1-2    3-9                 10-13      14
    obsah        režim  hodnota se znam.    jednotka   CR
    příklad      DC     -3.999              V          \\r

Tento modul je čistý Python bez závislostí: rozkládá proud bajtů na rámce,
rámce na hodnoty a odhaduje rozsah z formátu čísla. Nic nikam neposílá.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

FRAME_LEN = 14
REQUEST = b"D"          # jediný bajt, který software kdy do přístroje pošle

SERIAL_SETTINGS = dict(baudrate=1200, bytesize=7, parity="N", stopbits=2)

# Režim (znaky 1-2) -> (krátký popisek na „displeji“, dlouhý popis)
MODES: dict[str, tuple[str, str]] = {
    "DC": ("DC", "stejnosměrné"),
    "AC": ("AC", "střídavé"),
    "OH": ("Ω", "odpor"),
    "DI": ("▶|", "dioda"),
    "FR": ("FREQ", "frekvence"),
    "CA": ("CAP", "kapacita"),
    "HF": ("hFE", "zesílení tranzistoru"),
    "LO": ("LOGIC", "logická sonda"),
    "TE": ("TEMP", "teplota"),
}

# Předpony jednotek, jak je posílá přístroj (rozlišují se velká a malá písmena:
# "mV" = mili, "MOhm" = mega; kilo posílá jako velké "K").
_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
           "K": 1e3, "k": 1e3, "M": 1e6, "G": 1e9}
_BASE = {"V": "V", "A": "A", "Ohm": "Ω", "F": "F", "Hz": "Hz", "C": "°C"}
_PREFIX_SHOW = {"u": "µ", "K": "k"}

_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")


@dataclass(frozen=True)
class Unit:
    raw: str          # jak přišla z přístroje, např. "KOhm"
    base: str         # základní jednotka SI pro graf a statistiku, např. "Ω"
    factor: float     # násobek pro převod na základní jednotku
    label: str        # hezký popisek, např. "kΩ"


def parse_unit(raw: str) -> Unit:
    raw = raw.strip()
    if raw in _BASE:
        return Unit(raw, _BASE[raw], 1.0, _BASE[raw])
    if len(raw) > 1 and raw[0] in _PREFIX and raw[1:] in _BASE:
        p, b = raw[0], raw[1:]
        return Unit(raw, _BASE[b], _PREFIX[p], _PREFIX_SHOW.get(p, p) + _BASE[b])
    # neznámá jednotka (nebo žádná, např. hFE): ponecháme, jak přišla
    return Unit(raw, raw, 1.0, raw)


@dataclass
class Reading:
    """Jedno čtení z displeje."""
    mode: str                 # "DC", "AC", "OH", … (jak přišlo)
    text: str                 # hodnota jako text z displeje, např. "-0.001", "O.L"
    value: float | None       # číslo z displeje; None = přetížení nebo text
    unit: Unit
    overload: bool            # O.L
    range_value: float | None  # odhad plného rozsahu v jednotce rámce, např. 20
    raw: str                  # surový rámec bez CR
    t_wall: float = 0.0       # time.time() přijetí
    t_mono: float = 0.0       # time.monotonic() přijetí
    solicited: bool = True    # odpověď na náš dotaz (False = aktivní režim)
    extra: dict = field(default_factory=dict)

    @property
    def value_si(self) -> float | None:
        return None if self.value is None else self.value * self.unit.factor

    @property
    def mode_label(self) -> str:
        return MODES.get(self.mode, (self.mode, self.mode))[0]

    @property
    def mode_desc(self) -> str:
        return MODES.get(self.mode, (self.mode, "neznámý režim"))[1]

    @property
    def quantity(self) -> tuple[str, str]:
        """Klíč měřené veličiny. Změna klíče = otočný přepínač se pohnul jinam."""
        return (self.mode, self.unit.base)

    @property
    def range_label(self) -> str:
        if self.range_value is None:
            return "?"
        return f"{self.range_value:g} {self.unit.label}"


def _is_overload(s: str) -> bool:
    t = s.replace(" ", "").lstrip("+-").upper().replace("0", "O")
    return t in ("O.L", ".OL", "OL.", "OL", "O.L.")


def estimate_range(text: str, mode: str, unit_raw: str) -> float | None:
    """Odhad plného rozsahu z formátu čísla.

    Přístroj má 2000 bodů (3½ místa) a posílá úvodní nuly, takže počet číslic
    před tečkou odpovídá rozsahu: "0.000" -> 2, "00.00" -> 20, "000.0" -> 200.
    Bez tečky ("0000") jde o nejvyšší rozsah: 1000 V DC / 750 V AC, jinak 2000.
    """
    digits = text.replace(" ", "").lstrip("+-")
    if not digits or not _NUM_RE.match(digits):
        return None
    int_part, dot, frac = digits.partition(".")
    if dot and frac:
        return 2.0 * 10 ** (max(len(int_part), 1) - 1)
    if unit_raw.strip() == "V":
        return 750.0 if mode == "AC" else 1000.0
    return 2.0 * 10 ** (len(int_part) - 1) if len(int_part) >= 1 else None


def parse_frame(frame: bytes) -> Reading | None:
    """Rozparsuje jeden 14bajtový rámec (včetně koncového CR).

    Vrací None, pokud rámec nemá správnou délku, nekončí CR nebo obsahuje
    netisknutelné znaky. Rámec se správnou strukturou, jehož hodnota není číslo
    ani O.L (např. logická sonda „Hi“), vrátí Reading s value=None.
    """
    if len(frame) != FRAME_LEN or frame[-1:] != b"\r":
        return None
    body = bytes(b & 0x7F for b in frame[:-1])
    if any(b < 0x20 or b > 0x7E for b in body):
        return None
    text = body.decode("ascii")
    mode = text[0:2].strip()   # "  " = režim podle jednotky (dle sigrok)
    if not all(c.isalpha() for c in mode):
        return None
    val_s = text[2:9].strip()
    unit = parse_unit(text[9:13])
    if _is_overload(val_s):
        return Reading(mode, "O.L", None, unit, True,
                       None, text.rstrip())
    compact = val_s.replace(" ", "")
    if _NUM_RE.match(compact):
        return Reading(mode, compact, float(compact), unit, False,
                       estimate_range(compact, mode, unit.raw), text.rstrip())
    return Reading(mode, val_s, None, unit, False, None, text.rstrip())


class FrameSplitter:
    """Skládá rámce z proudu bajtů. Odolné proti useknutým rámcům, šumu
    a přerušení: dělí podle CR a z každého kusu bere posledních 14 bajtů."""

    MAX_BUF = 256

    def __init__(self) -> None:
        self.buf = bytearray()
        self.fragments = 0   # kusy kratší než rámec (neplatné)
        self.resyncs = 0     # kusy s nadbytečnými bajty před rámcem

    def feed(self, data: bytes) -> list[bytes]:
        out: list[bytes] = []
        self.buf += data
        while True:
            i = self.buf.find(b"\r")
            if i < 0:
                break
            chunk = bytes(self.buf[: i + 1])
            del self.buf[: i + 1]
            if len(chunk) < FRAME_LEN:
                self.fragments += 1
                out.append(chunk)       # předáme dál, parser ho odmítne
                continue
            if len(chunk) > FRAME_LEN:
                self.resyncs += 1
                chunk = chunk[-FRAME_LEN:]
            out.append(chunk)
        if len(self.buf) > self.MAX_BUF:      # šum bez CR – zahodit
            self.fragments += 1
            del self.buf[:-FRAME_LEN]
        return out

    def clear(self) -> None:
        self.buf.clear()


def format_number(x: float, digits: int = 4) -> str:
    """Číslo pro statistiky: 4 platné číslice, bez exponentu pro běžné hodnoty."""
    if x is None or isinstance(x, float) and math.isnan(x):
        return "—"
    if x == 0:
        return "0"
    mag = math.floor(math.log10(abs(x)))
    dec = max(digits - 1 - mag, 0)
    return f"{x:.{dec}f}"


SI_PREFIXES = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"),
               (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")]


def parse_si(text: str) -> float | None:
    """Číslo zadané uživatelem s volitelnou předponou: "15m", "1,2 k", "0.015"."""
    s = text.strip().replace(",", ".").replace(" ", "")
    if not s:
        return None
    mult = 1.0
    # odstranit případnou jednotku na konci (V, A, Ω, Ohm, F, Hz)
    for u in ("Ohm", "ohm", "Ω", "Hz", "V", "A", "F"):
        if s.endswith(u) and len(s) > len(u):
            s = s[: -len(u)]
            break
    table = {"G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3, "m": 1e-3, "u": 1e-6,
             "µ": 1e-6, "n": 1e-9, "p": 1e-12}
    if s and s[-1] in table:
        mult = table[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None
