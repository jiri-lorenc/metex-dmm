# SPDX-License-Identifier: GPL-3.0-or-later
"""Statistiky měření: za celý běh (řadu) a v klouzavém časovém okně.

Hodnoty se vedou v základních jednotkách SI (V, A, Ω, F, Hz), takže změna
rozsahu (např. 20 mA -> 200 mA) statistiku nerozbije. Přetížení (O.L)
se jen počítají, do průměru ani min/max nevstupují.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass
class Summary:
    n: int = 0
    mean: float = math.nan
    min: float = math.nan
    max: float = math.nan
    std: float = math.nan


class RunStats:
    """Průběžný průměr, rozptyl (Welfordův algoritmus), min, max."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.n = 0
        self._mean = 0.0
        self._m2 = 0.0
        self.min = math.inf
        self.max = -math.inf
        self.overloads = 0

    def add(self, v: float) -> None:
        self.n += 1
        d = v - self._mean
        self._mean += d / self.n
        self._m2 += d * (v - self._mean)
        if v < self.min:
            self.min = v
        if v > self.max:
            self.max = v

    def summary(self) -> Summary:
        if not self.n:
            return Summary()
        std = math.sqrt(self._m2 / (self.n - 1)) if self.n > 1 else 0.0
        return Summary(self.n, self._mean, self.min, self.max, std)


class WindowStats:
    """Statistika vzorků za posledních ``seconds`` sekund."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.buf: deque[tuple[float, float]] = deque()
        self.overloads: deque[float] = deque()

    def reset(self) -> None:
        self.buf.clear()
        self.overloads.clear()

    def _trim(self, now: float) -> None:
        lim = now - self.seconds
        while self.buf and self.buf[0][0] < lim:
            self.buf.popleft()
        while self.overloads and self.overloads[0] < lim:
            self.overloads.popleft()

    def add(self, t: float, v: float) -> None:
        self.buf.append((t, v))
        self._trim(t)

    def add_overload(self, t: float) -> None:
        self.overloads.append(t)
        self._trim(t)

    def summary(self, now: float | None = None) -> Summary:
        if now is not None:
            self._trim(now)
        if not self.buf:
            return Summary()
        vals = [v for _, v in self.buf]
        n = len(vals)
        mean = sum(vals) / n
        std = (math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
               if n > 1 else 0.0)
        return Summary(n, mean, min(vals), max(vals), std)

    @property
    def overload_count(self) -> int:
        return len(self.overloads)
