"""Simulátor přístroje pro vyzkoušení aplikace bez hardwaru.

Chová se jako otevřený sériový port: na bajt ``D`` po zhruba 0,3 s vrátí
14bajtový rámec. Volitelně vysílá sám jako skutečný M-3650D v režimu COM:
~2,5× za sekundu (mezery 0,4 a 0,6 s) a přitom dál odpovídá i na ``D``.
Hodnota: pomalu se měnící stejnosměrné napětí kolem 12 V na rozsahu 20 V,
občas krátké přetížení (O.L), aby bylo vidět, jak s ním aplikace zachází.
"""
from __future__ import annotations

import math
import random
import time


def make_frame(mode: str, value: str, unit: str) -> bytes:
    """Sestaví rámec: režim 2 znaky, hodnota 7 znaků, jednotka 4 znaky, CR."""
    return f"{mode:<2.2}{value:>7.7}{unit:>4.4}\r".encode("ascii")


class SimulatedMeter:
    REPLY_DELAY = 0.3

    def __init__(self, active: bool = False, seed: int | None = None) -> None:
        self.active = active
        self.rng = random.Random(seed)
        self.t0 = time.monotonic()
        self._pending: list[tuple[float, bytes]] = []
        self._next_active = self.t0 + 0.4
        self._gap = 0.4
        self.is_open = True
        self.written = bytearray()

    # rozhraní podobné pyserial
    @property
    def in_waiting(self) -> int:
        self._tick()
        now = time.monotonic()
        return sum(len(f) for t, f in self._pending if t <= now)

    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise OSError("port zavřen")
        self.written += data
        for b in data:
            if b == ord("D"):
                self._pending.append((time.monotonic() + self.REPLY_DELAY,
                                      self.frame()))
        return len(data)

    def read(self, n: int = 1) -> bytes:
        if not self.is_open:
            raise OSError("port zavřen")
        deadline = time.monotonic() + 0.05
        while True:
            self._tick()
            now = time.monotonic()
            ready = b"".join(f for t, f in self._pending if t <= now)
            if ready or now >= deadline:
                break
            time.sleep(0.01)
        self._pending = [(t, f) for t, f in self._pending if t > now]
        return ready  # celé hotové rámce najednou stačí

    def close(self) -> None:
        self.is_open = False

    def _tick(self) -> None:
        now = time.monotonic()
        if self.active and now >= self._next_active:
            self._pending.append((now, self.frame()))
            self._gap = 1.0 - self._gap          # 0,4 / 0,6 s
            self._next_active = now + self._gap

    def frame(self) -> bytes:
        t = time.monotonic() - self.t0
        v = 12.0 + 3.0 * math.sin(t / 20.0) + self.rng.gauss(0, 0.02)
        if self.rng.random() < 0.01:
            return make_frame("DC", "O.L", "V")
        return make_frame("DC", f"{v:05.2f}", "V")
