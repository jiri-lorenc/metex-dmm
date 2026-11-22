#!/usr/bin/env python3
"""Řádkový logger Metex M-3650D (bez GUI) – např. pro dlouhé měření.

Používá stejné jádro jako aplikace (metex_dmm.link / protocol / csvlog).
Do přístroje posílá jen dotaz 'D' (v režimu --listen nic).

  python tools/metex_log.py --list
  python tools/metex_log.py --port COM10 --raw --duration 10
  python tools/metex_log.py --port COM10 --out mereni.csv --window 60
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from metex_dmm.csvlog import CsvLogger  # noqa: E402
from metex_dmm.link import MetexLink, PollMode, list_ports  # noqa: E402
from metex_dmm.protocol import format_number  # noqa: E402
from metex_dmm.stats import RunStats, WindowStats  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Logger multimetru Metex M-3650D")
    ap.add_argument("--port", help="COM port převodníku, např. COM10 "
                    "(SIMULÁTOR = bez přístroje)")
    ap.add_argument("--out", help="CSV soubor (připisuje se na konec)")
    ap.add_argument("--excel", action="store_true",
                    help="CSV pro český Excel (středník, desetinná čárka)")
    ap.add_argument("--window", type=float, default=60.0,
                    help="klouzavé okno statistiky v s (výchozí 60)")
    ap.add_argument("--report", type=float, default=10.0,
                    help="jak často vypsat souhrn, s (výchozí 10)")
    ap.add_argument("--interval", type=float, default=0.0,
                    help="min. odstup dotazů v s (0 = co nejrychleji)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="skončit po N sekundách (0 = do Ctrl+C)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--poll", action="store_true",
                      help="vždy se ptát 'D' (pasivní režim)")
    mode.add_argument("--listen", action="store_true",
                      help="nic neposílat, jen poslouchat (aktivní režim COM)")
    ap.add_argument("--raw", action="store_true", help="vypisovat každý rámec")
    ap.add_argument("--list", action="store_true", help="vypsat COM porty")
    a = ap.parse_args()

    if a.list:
        for dev, desc in list_ports():
            print(f"{dev:8} {desc}")
        return 0
    if not a.port:
        ap.error("chybí --port (seznam portů: --list)")

    run, win = RunStats(), WindowStats(a.window)
    log = CsvLogger(a.out, a.excel) if a.out else None
    unit = {"u": None}
    lock = threading.Lock()

    def on_reading(r):
        with lock:
            if a.raw:
                print(f"{r.raw!r}  ->  {r.text} {r.unit.label} "
                      f"({'odpověď' if r.solicited else 'samovolně'})", flush=True)
            if log:
                log.write(r)
            if r.overload:
                run.overloads += 1
                win.add_overload(r.t_mono)
            elif r.value_si is not None:
                if unit["u"] not in (None, r.unit.base):
                    print(f"[!] změna veličiny na {r.unit.base} – statistika "
                          "vynulována", file=sys.stderr, flush=True)
                    run.reset()
                    win.reset()
                unit["u"] = r.unit.base
                run.add(r.value_si)
                win.add(r.t_mono, r.value_si)

    def on_state(state, msg):
        print(f"[{state.value}] {msg}", file=sys.stderr, flush=True)

    pm = PollMode.POLL if a.poll else PollMode.LISTEN if a.listen else PollMode.AUTO
    link = MetexLink(a.port, pm, a.interval, on_reading=on_reading,
                     on_state=on_state)
    link.start()
    t0 = time.monotonic()
    last = t0
    print(f"Čtu {a.port} (1200 7N2, DTR=1 RTS=0, režim {pm.value}). "
          "Ctrl+C ukončí.", flush=True)
    f = format_number
    try:
        while True:
            time.sleep(0.2)
            now = time.monotonic()
            if now - last >= a.report:
                last = now
                with lock:
                    s, w = run.summary(), win.summary(now)
                print(f"{time.strftime('%H:%M:%S')}  n={s.n}  průměr={f(s.mean)} "
                      f"min={f(s.min)} max={f(s.max)} | okno {a.window:g} s: "
                      f"průměr={f(w.mean)} min={f(w.min)} max={f(w.max)} n={w.n}"
                      f"  O.L={run.overloads}  [{unit['u'] or '?'}]", flush=True)
            if a.duration and now - t0 >= a.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        link.stop()
        if log:
            log.close()
    st = link.stats
    s = run.summary()
    dur = time.monotonic() - t0
    print("\n=== Souhrn ===")
    print(f"doba {dur:.0f} s, platných vzorků {s.n} ({s.n / dur if dur else 0:.2f}/s), "
          f"chybných rámců {st.frames_bad}, bez odpovědi {st.timeouts}, "
          f"O.L {run.overloads}, znovupřipojení {st.reconnects}")
    print(f"průměr {f(s.mean)}  min {f(s.min)}  max {f(s.max)}  "
          f"σ {f(s.std)}  [{unit['u'] or '?'}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
