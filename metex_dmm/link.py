"""Sériová komunikace s multimetrem ve vlastním vlákně.

Zásady:
* Do přístroje se posílá výhradně bajt ``D`` (dotaz na hodnotu). Nic jiného,
  žádné nastavování. V režimu „jen poslouchat“ se neposílá vůbec nic.
* Výpadky nevadí: neplatné rámce se počítají a zahazují, ticho na lince se
  hlásí, odpojení převodníku (USB) vede k zavření portu a opakovaným pokusům
  o znovuotevření, dokud se port neobjeví.
* Režim AUTO po otevření portu 1,5 s a pak jednou za 20 s na 0,8 s jen
  poslouchá. Přijde-li v té chvíli rámec bez dotazu, přístroj je v aktivním
  režimu (symbol COM, vysílá sám ~2,5×/s) a software se přestane ptát. Když samovolné
  vysílání ustane (po otočení přepínačem se přístroj vrací do pasivního
  režimu), začne se zase ptát.

Modul nezávisí na Qt; výsledky předává přes callbacky (volané z vlákna linky).
"""
from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .protocol import REQUEST, FrameSplitter, Reading, parse_frame

SIM_PORT = "SIMULÁTOR"


class PollMode(enum.Enum):
    AUTO = "auto"        # ptát se 'D', ale v aktivním režimu jen poslouchat
    POLL = "poll"        # vždy se ptát 'D' (pasivní režim)
    LISTEN = "listen"    # nikdy nic neposílat (aktivní režim, COM)


class LinkState(enum.Enum):
    STOPPED = "odpojeno"
    OPENING = "otevírám port"
    WAITING_PORT = "port nedostupný – čekám"
    NO_DATA = "port otevřen, přístroj neodpovídá"
    RECEIVING = "přijímám data"


@dataclass
class LinkStats:
    frames_ok: int = 0
    frames_bad: int = 0
    requests: int = 0
    timeouts: int = 0
    reconnects: int = 0
    active_mode: bool = False      # přístroj vysílá sám (COM)
    last_rx_mono: float = 0.0
    last_raw: bytes = b""


class MetexLink:
    REPLY_TIMEOUT = 1.0     # s, na odpověď na 'D' (rámec trvá ~117 ms)
    REOPEN_DELAY = 2.0      # s mezi pokusy o otevření portu
    ACTIVE_SILENCE = 2.5    # s bez samovolných rámců -> zpět do pasivního
    NO_DATA_AFTER = 3.0     # s ticha -> stav NO_DATA
    # AUTO: jednou za PROBE_EVERY se na PROBE_LEN přestane ptát a jen poslouchá.
    # Ověřeno s M-3650D: v aktivním režimu (COM) vysílá ~2,5×/s (mezery 0,4
    # a 0,6 s) a NA 'D' DÁL ODPOVÍDÁ, takže ho podle chybějících odpovědí poznat
    # nejde – jen podle rámců, které přijdou, když se neptáme.
    PROBE_LEN = 0.8
    PROBE_FIRST = 1.5       # po otevření portu poslouchat déle
    PROBE_EVERY = 20.0
    # Odpověď na 'D' nemůže dorazit dřív, než se odvysílá: 14 znaků × 10 bitů
    # při 1200 Bd = 117 ms. Rámec hotový dřív musel začít před dotazem, tedy
    # ho přístroj poslal sám (aktivní režim).
    MIN_REPLY = 0.11
    PROBE_AFTER_TIMEOUTS = 2  # tolik dotazů bez odpovědi za sebou -> naslouchat

    def __init__(self, port: str, poll_mode: PollMode = PollMode.AUTO,
                 poll_interval: float = 0.0,
                 on_reading: Callable[[Reading], None] | None = None,
                 on_state: Callable[[LinkState, str], None] | None = None,
                 on_bad: Callable[[bytes], None] | None = None,
                 serial_factory: Callable[[str], object] | None = None) -> None:
        self.port = port
        self.poll_mode = poll_mode
        self.poll_interval = poll_interval     # min. odstup dotazů, 0 = co nejrychleji
        self.on_reading = on_reading or (lambda r: None)
        self.on_state = on_state or (lambda s, m: None)
        self.on_bad = on_bad or (lambda b: None)
        self.serial_factory = serial_factory or open_serial
        self.stats = LinkStats()
        self.state = LinkState.STOPPED
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ser = None

    # ---- řízení ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="metex-link",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---- vnitřek --------------------------------------------------------
    def _set_state(self, state: LinkState, msg: str = "") -> None:
        if state != self.state or msg:
            self.state = state
            try:
                self.on_state(state, msg)
            except Exception:  # noqa: BLE001 – chyba v UI nesmí shodit linku
                pass

    def _close(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        first = True
        while not self._stop.is_set():
            self._set_state(LinkState.OPENING, f"otevírám {self.port}")
            try:
                self._ser = self.serial_factory(self.port)
            except Exception as e:  # noqa: BLE001 – port chybí, je obsazený…
                self._set_state(LinkState.WAITING_PORT, f"{self.port}: {e}")
                self._stop.wait(self.REOPEN_DELAY)
                continue
            if not first:
                self.stats.reconnects += 1
            first = False
            try:
                self._session()
            except Exception as e:  # noqa: BLE001 – odpojení převodníku apod.
                self._set_state(LinkState.WAITING_PORT,
                                f"spojení přerušeno: {e}")
            finally:
                self._close()
            if not self._stop.is_set():
                self._stop.wait(self.REOPEN_DELAY / 2)
        self._close()
        self._set_state(LinkState.STOPPED, "")

    def _session(self) -> None:
        ser = self._ser
        splitter = FrameSplitter()
        st = self.stats
        outstanding = False
        req_t = 0.0
        next_poll = 0.0
        opened = time.monotonic()
        last_unsolicited = -1e9
        missed = 0
        # AUTO: na začátku a pak jednou za PROBE_EVERY chvíli jen posloucháme;
        # rámec, který přijde bez dotazu, znamená aktivní režim (COM).
        probe_until = opened + self.PROBE_FIRST
        next_probe = probe_until + self.PROBE_EVERY
        st.active_mode = False
        self._set_state(LinkState.NO_DATA, f"{self.port} otevřen, čekám na data")
        while not self._stop.is_set():
            now = time.monotonic()
            if st.active_mode and now - last_unsolicited > self.ACTIVE_SILENCE:
                st.active_mode = False          # přístroj přestal vysílat sám
            auto = self.poll_mode == PollMode.AUTO
            if outstanding and now - req_t > self.REPLY_TIMEOUT:
                outstanding = False
                st.timeouts += 1
                missed += 1
                if auto and missed >= self.PROBE_AFTER_TIMEOUTS:
                    next_probe = now      # neodpovídá: zkusit jen poslouchat
                    missed = 0
            if (auto and not st.active_mode and not outstanding
                    and now >= next_probe):
                probe_until = now + self.PROBE_LEN
                next_probe = probe_until + self.PROBE_EVERY
            send = (self.poll_mode == PollMode.POLL
                    or (auto and not st.active_mode and now >= probe_until))
            if send and not outstanding and now >= next_poll:
                ser.write(REQUEST)
                st.requests += 1
                outstanding = True
                req_t = now
            data = ser.read(max(1, getattr(ser, "in_waiting", 0) or 0))
            now = time.monotonic()
            if data:
                for chunk in splitter.feed(data):
                    r = parse_frame(chunk)
                    st.last_raw = chunk
                    if r is None:
                        st.frames_bad += 1
                        try:
                            self.on_bad(chunk)
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    r.t_mono = now
                    r.t_wall = time.time()
                    if outstanding and now - req_t >= self.MIN_REPLY:
                        outstanding = False
                        missed = 0
                        r.solicited = True
                        next_poll = now + self.poll_interval
                    else:
                        r.solicited = False
                        last_unsolicited = now
                        if self.poll_mode != PollMode.POLL:
                            st.active_mode = True
                    st.frames_ok += 1
                    st.last_rx_mono = now
                    self._set_state(LinkState.RECEIVING)
                    try:
                        self.on_reading(r)
                    except Exception:  # noqa: BLE001
                        pass
            last = st.last_rx_mono if st.last_rx_mono > opened else opened
            if now - last > self.NO_DATA_AFTER and self.state != LinkState.NO_DATA:
                self._set_state(LinkState.NO_DATA)


def open_serial(port: str):
    """Otevře port s parametry přístroje. Pro SIM_PORT vrátí simulátor."""
    if port == SIM_PORT:
        from .simulator import SimulatedMeter
        return SimulatedMeter()
    import serial  # pyserial

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 1200
    ser.bytesize = serial.SEVENBITS
    ser.parity = serial.PARITY_NONE
    ser.stopbits = serial.STOPBITS_TWO
    ser.timeout = 0.05
    ser.write_timeout = 1.0
    ser.xonxoff = ser.rtscts = ser.dsrdtr = False
    # Napájení optočlenu v kabelu: DTR kladně, RTS záporně (nastavit před open)
    ser.dtr = True
    ser.rts = False
    ser.open()
    time.sleep(0.2)            # ustálení napájení optočlenu
    ser.reset_input_buffer()
    return ser


def list_ports() -> list[tuple[str, str]]:
    """[(zařízení, popis)], seřazeno; převodníky FTDI/Prolific/CH340 první."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    out = []
    for p in lp.comports():
        vidpid = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else ""
        desc = " ".join(x for x in (p.description, vidpid) if x)
        out.append((p.device, desc))

    def key(item):
        dev = item[0]
        num = int("".join(c for c in dev if c.isdigit()) or 0)
        return (0 if "0403:" in item[1] or "067B:" in item[1]
                or "1A86:" in item[1] else 1, num)
    return sorted(out, key=key)
