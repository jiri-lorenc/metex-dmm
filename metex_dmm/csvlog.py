# SPDX-License-Identifier: GPL-3.0-or-later
"""Zápis měření do CSV.

Dva formáty:
* ``excel_cz=True``  – oddělovač středník, desetinná čárka, UTF-8 s BOM;
  soubor se v českém Excelu otevře dvojklikem rovnou do sloupců.
* ``excel_cz=False`` – oddělovač čárka, desetinná tečka (Python, R, gnuplot…).
"""
from __future__ import annotations

import csv
import datetime as dt
import os

from .protocol import Reading

COLUMNS = ["time_iso", "elapsed_s", "mode", "value", "unit", "value_si",
           "unit_si", "range", "overload", "alarm", "raw"]


def _iso(t_wall: float) -> str:
    return (dt.datetime.fromtimestamp(t_wall).astimezone()
            .isoformat(timespec="milliseconds"))


class CsvLogger:
    def __init__(self, path: str, excel_cz: bool = True) -> None:
        self.path = path
        self.excel_cz = excel_cz
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._f = open(path, "a", newline="",
                       encoding="utf-8-sig" if excel_cz and new else "utf-8")
        self._w = csv.writer(self._f, delimiter=";" if excel_cz else ",")
        self.rows = 0
        self.t0: float | None = None
        if new:
            self._w.writerow(COLUMNS)
            self._f.flush()

    def _num(self, x: float | None, fmt: str = "{:.6g}") -> str:
        if x is None:
            return ""
        s = fmt.format(x)
        return s.replace(".", ",") if self.excel_cz else s

    def write(self, r: Reading, alarm: str = "", range_label: str = "") -> None:
        if self.t0 is None:
            self.t0 = r.t_wall
        self._w.writerow([
            _iso(r.t_wall),
            self._num(r.t_wall - self.t0, "{:.3f}"),
            r.mode,
            "" if r.overload else (r.text if r.value is None
                                   else self._num(r.value, "{:g}")),
            r.unit.raw,
            self._num(r.value_si, "{:.6g}"),
            r.unit.base,
            range_label or r.range_label,
            "1" if r.overload else "0",
            alarm,
            r.raw,
        ])
        self.rows += 1
        self._f.flush()

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:  # noqa: BLE001
            pass


def write_csv(path: str, readings: list[tuple[Reading, str, str]],
              excel_cz: bool = True) -> int:
    """Uloží seznam (čtení, alarm, rozsah) do nového souboru. Vrací počet řádků."""
    if os.path.exists(path):
        os.remove(path)
    log = CsvLogger(path, excel_cz)
    try:
        for r, alarm, rng in readings:
            log.write(r, alarm, rng)
    finally:
        log.close()
    return log.rows
