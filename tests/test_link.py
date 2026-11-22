"""Testy sériové vrstvy proti náhradnímu portu (bez hardwaru)."""
import threading
import time

from metex_dmm.link import LinkState, MetexLink, PollMode
from metex_dmm.simulator import SimulatedMeter, make_frame


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def run_link(factory, mode=PollMode.AUTO, **kw):
    readings, states = [], []
    link = MetexLink("TEST", mode, on_reading=readings.append,
                     on_state=lambda s, m: states.append(s),
                     serial_factory=factory, **kw)
    link.REOPEN_DELAY = 0.1
    link.start()
    return link, readings, states


def test_passive_polling_sends_only_D():
    meters = []

    def factory(port):
        m = SimulatedMeter(seed=1)
        m.REPLY_DELAY = 0.15
        meters.append(m)
        return m

    link, readings, _ = run_link(factory, PollMode.POLL)
    try:
        assert wait_for(lambda: len(readings) >= 5)
    finally:
        link.stop()
    assert set(bytes(meters[0].written)) == {ord("D")}
    assert all(r.solicited for r in readings)
    assert link.stats.frames_ok >= 5


def test_listen_mode_sends_nothing_and_receives_active_frames():
    meters = []

    def factory(port):
        m = SimulatedMeter(active=True, seed=2)
        meters.append(m)
        return m

    link, readings, _ = run_link(factory, PollMode.LISTEN)
    try:
        assert wait_for(lambda: len(readings) >= 2, 4)
    finally:
        link.stop()
    assert bytes(meters[0].written) == b""
    assert not any(r.solicited for r in readings)


def test_auto_detects_active_mode_and_stops_polling():
    meters = []

    def factory(port):
        m = SimulatedMeter(active=True, seed=3)
        m.REPLY_DELAY = 10.0     # na dotaz „neodpovídá“, jen vysílá sám
        meters.append(m)
        return m

    link, readings, _ = run_link(factory, PollMode.AUTO)
    try:
        assert wait_for(lambda: link.stats.active_mode, 6)
        sent = len(meters[0].written)
        time.sleep(1.5)
        assert len(meters[0].written) == sent   # v aktivním režimu se neptá
    finally:
        link.stop()


class Flaky:
    """Port, který se po pár rámcích „odpojí“ (jako vytažení USB)."""
    opened = 0

    def __init__(self):
        Flaky.opened += 1
        self.inner = SimulatedMeter(seed=4)
        self.inner.REPLY_DELAY = 0.15
        self.reads = 0

    @property
    def in_waiting(self):
        return self.inner.in_waiting

    def write(self, d):
        return self.inner.write(d)

    def read(self, n=1):
        self.reads += 1
        if Flaky.opened == 1 and self.reads > 30:
            raise OSError("ClearCommError failed (zařízení odpojeno)")
        return self.inner.read(n)

    def close(self):
        self.inner.close()


def test_reconnect_after_unplug():
    Flaky.opened = 0
    attempts = {"n": 0}

    def factory(port):
        attempts["n"] += 1
        if attempts["n"] in (2, 3):
            raise OSError("could not open port 'COM10'")   # port zatím chybí
        return Flaky()

    link, readings, states = run_link(factory, PollMode.POLL)
    try:
        assert wait_for(lambda: Flaky.opened >= 2 and
                        link.state == LinkState.RECEIVING, 8)
        n = len(readings)
        assert wait_for(lambda: len(readings) > n + 2)
    finally:
        link.stop()
    assert LinkState.WAITING_PORT in states
    assert link.stats.reconnects >= 1


class Garbage:
    """Port, který posílá směs šumu, useknutých a platných rámců."""

    def __init__(self):
        self.data = bytearray(b"\x00\x7fzz\r" + b"DC  1" + b"\r" +
                              make_frame("DC", "12.34", "V") +
                              b"\xff\xfe" + make_frame("DC", "O.L", "V") +
                              make_frame("DC", "12.35", "V"))
        self.lock = threading.Lock()

    in_waiting = 0

    def write(self, d):
        return len(d)

    def read(self, n=1):
        time.sleep(0.01)
        with self.lock:
            out = bytes(self.data[:5])
            del self.data[:5]
        return out

    def close(self):
        pass


def test_garbage_does_not_crash():
    link, readings, _ = run_link(lambda p: Garbage(), PollMode.LISTEN)
    try:
        assert wait_for(lambda: len(readings) >= 3)
    finally:
        link.stop()
    assert [r.value for r in readings][:3] == [12.34, None, 12.35]
    assert readings[1].overload
    assert link.stats.frames_bad >= 2


def test_ui_callback_exception_does_not_kill_link():
    def boom(r):
        raise RuntimeError("chyba v UI")

    m = SimulatedMeter(seed=5)
    m.REPLY_DELAY = 0.15
    link = MetexLink("TEST", PollMode.POLL, on_reading=boom,
                     serial_factory=lambda p: m)
    link.start()
    try:
        assert wait_for(lambda: link.stats.frames_ok >= 3)
        assert link.running
    finally:
        link.stop()


def test_auto_active_mode_found_after_switch_while_polling():
    """Uživatel stiskne FUNCTION (COM) až během dotazování: přístroj na 'D'
    přestane odpovídat a vysílá sám. AUTO to musí poznat a přestat se ptát."""
    m = SimulatedMeter(seed=6)
    m.REPLY_DELAY = 0.15
    link, readings, _ = run_link(lambda p: m, PollMode.AUTO)
    link.PROBE_LEN = 0.3
    try:
        assert wait_for(lambda: any(r.solicited for r in readings), 4)
        m.REPLY_DELAY = 1e9        # na dotaz už neodpovídá
        m.active = True            # …ale vysílá sám
        assert wait_for(lambda: link.stats.active_mode, 8)
        sent = len(m.written)
        time.sleep(1.5)
        assert len(m.written) == sent
    finally:
        link.stop()


def test_early_frame_is_not_taken_as_reply():
    """Rámec hotový dřív než za 117 ms po dotazu je samovolný (aktivní režim)."""
    m = SimulatedMeter(active=True, seed=7)
    m.REPLY_DELAY = 1e9
    link, readings, _ = run_link(lambda p: m, PollMode.POLL)
    try:
        assert wait_for(lambda: len(readings) >= 2, 5)
    finally:
        link.stop()
    # v režimu POLL se ptá dál, ale žádný rámec nesmí být brán jako odpověď
    # dřív než MIN_REPLY po dotazu
    assert link.stats.frames_ok >= 2


def test_auto_detects_real_com_behaviour():
    """Skutečné chování M-3650D v COM (ověřeno 22. 11. 2026): vysílá ~2,5×/s
    a zároveň dál odpovídá na 'D'. AUTO to musí poznat a přestat se ptát."""
    m = SimulatedMeter(seed=8)
    m.REPLY_DELAY = 0.25
    link, readings, _ = run_link(lambda p: m, PollMode.AUTO)
    link.PROBE_EVERY = 2.0
    try:
        assert wait_for(lambda: len(readings) >= 3, 5)
        assert not link.stats.active_mode
        m.active = True                    # uživatel stiskl FUNCTION -> COM
        assert wait_for(lambda: link.stats.active_mode, 8)
        sent = len(m.written)
        time.sleep(1.5)
        assert len(m.written) == sent      # v COM se už neptá
        n = len(readings)
        m.active = False                   # otočení přepínačem -> pasivní
        assert wait_for(lambda: not link.stats.active_mode, 5)
        assert wait_for(lambda: len(m.written) > sent and len(readings) > n + 2, 5)
    finally:
        link.stop()
