# SPDX-License-Identifier: GPL-3.0-or-later
"""Hlavní okno aplikace: displej, graf, statistiky, záznam, alarmy."""
from __future__ import annotations

import datetime as dt
import math
import os
import sys
import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QSplitter, QStatusBar, QVBoxLayout,
                               QWidget)

from . import __version__
from .csvlog import CsvLogger, write_csv
from .lcd import LcdPanel
from .link import SIM_PORT, LinkState, MetexLink, PollMode, list_ports
from .protocol import Reading, format_number, parse_si
from .stats import RunStats, WindowStats

APP = "Metex M-3650D"
MAX_POINTS = 1_000_000          # paměť grafu/exportu (~4 dny při 3 vzorcích/s)

WINDOWS = [("10 s", 10), ("30 s", 30), ("1 min", 60), ("5 min", 300),
           ("15 min", 900), ("1 h", 3600)]
GRAPH_SPANS = [("30 s", 30), ("1 min", 60), ("5 min", 300), ("15 min", 900),
               ("1 h", 3600), ("6 h", 21600), ("vše", 0)]
INTERVALS = [("co nejrychleji (~3/s)", 0.0), ("0,5 s", 0.5), ("1 s", 1.0),
             ("2 s", 2.0), ("5 s", 5.0), ("10 s", 10.0), ("30 s", 30.0),
             ("1 min", 60.0)]
POLL_MODES = [("Automaticky (doporučeno)", PollMode.AUTO),
              ("Pasivní – dotaz „D“", PollMode.POLL),
              ("Aktivní (COM) – jen poslouchat", PollMode.LISTEN)]

STATE_COLORS = {LinkState.STOPPED: "#6b7680", LinkState.OPENING: "#d9a441",
                LinkState.WAITING_PORT: "#e5484d", LinkState.NO_DATA: "#d9a441",
                LinkState.RECEIVING: "#3dbb6c"}

RANGE_NOTE = ("Rozsah se přepíná jen ručně otočným přepínačem na přístroji – "
              "software ho nastavit nemůže a nebude.")
CURRENT_NOTE = ("Měříte PROUD: přepnutím otočného přepínače se měřený obvod "
                "přeruší (a na krajních polohách může dojít k přepálení "
                "pojistky). Rozsah volte předem s rezervou.")


class Bridge(QObject):
    reading = Signal(object)
    state = Signal(object, str)


def apply_dark_palette(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    base, alt, text = QColor("#15191d"), QColor("#1c2126"), QColor("#d7dde2")
    pal.setColor(QPalette.Window, QColor("#1a1e22"))
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, base)
    pal.setColor(QPalette.AlternateBase, alt)
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.Button, QColor("#252b31"))
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.Highlight, QColor("#2f6f9f"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, alt)
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.PlaceholderText, QColor("#6b7680"))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, QColor("#5b646d"))
    app.setPalette(pal)
    app.setStyleSheet("""
        QGroupBox { border: 1px solid #2a3138; border-radius: 4px;
                    margin-top: 14px; padding: 8px 6px 6px 6px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px;
                           color: #8a949e; font-weight: 600; }
        QPushButton { padding: 5px 12px; border: 1px solid #353d45;
                      border-radius: 3px; background: #252b31; }
        QPushButton:hover { background: #2d343b; }
        QPushButton:checked { background: #2f6f9f; border-color: #3b84bb; }
        QPushButton:disabled { color: #5b646d; }
        QPushButton#rec { color: #ff6b6f; font-weight: 600; }
        QLabel#note { color: #b9c2ca; padding: 4px 8px;
                      border-left: 3px solid #d9a441; background: #20252a; }
        QLabel#warn { color: #ffd7d8; padding: 6px 8px; font-weight: 600;
                      border-left: 3px solid #e5484d; background: #3a1f22; }
        QLabel[class="k"] { color: #8a949e; }
        QLabel[class="v"] { font-family: Consolas, monospace; font-size: 10.5pt; }
        QLabel[class="h"] { color: #8a949e; font-weight: 600; }
        QStatusBar QLabel { color: #8a949e; padding: 0 6px; }
    """)


class MainWindow(QMainWindow):
    def __init__(self, demo: bool = False, port: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP} – měření v počítači")
        self.settings = QSettings("metex-dmm", "metex-dmm")
        self.bridge = Bridge()
        self.bridge.reading.connect(self.on_reading)
        self.bridge.state.connect(self.on_state)
        self.link: MetexLink | None = None
        self.logger: CsvLogger | None = None
        self.rec_paused = False
        self.rec_t0 = 0.0
        self.quantity: tuple[str, str] | None = None
        self.last: Reading | None = None
        self.last_range: dict[tuple, tuple[float, str]] = {}
        self.run = RunStats()
        self.win = WindowStats(60)
        self.series_t0 = time.time()
        self.t: list[float] = []
        self.v: list[float] = []
        self.ol_t: list[float] = []
        self.ol_v: list[float] = []
        self.records: deque = deque(maxlen=MAX_POINTS)
        self.rx_times: deque[float] = deque(maxlen=12)
        self.alarm_state = ""
        self.alarm_events = 0
        self.dirty = False
        self._build_ui()
        self._load_settings(demo, port)
        self.timer = QTimer(self, interval=250, timeout=self._tick)
        self.timer.start()
        self.blink_timer = QTimer(self, interval=400, timeout=self._blink)
        self.blink_timer.start()

    # ================================================================ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 4)
        root.setSpacing(8)

        # --- připojení ---
        conn = QHBoxLayout()
        conn.addWidget(QLabel("Port:"))
        self.cb_port = QComboBox(minimumWidth=260)
        conn.addWidget(self.cb_port)
        b = QPushButton("Obnovit", toolTip="Znovu načíst seznam COM portů")
        b.clicked.connect(self.refresh_ports)
        conn.addWidget(b)
        conn.addSpacing(10)
        conn.addWidget(QLabel("Komunikace:"))
        self.cb_mode = QComboBox()
        for label, m in POLL_MODES:
            self.cb_mode.addItem(label, m)
        self.cb_mode.setToolTip(
            "Automaticky: ptá se dotazem „D“ (pasivní režim) a pozná, když\n"
            "přístroj vysílá sám (aktivní režim, na displeji COM) – pak se\n"
            "neptá a jen poslouchá.\nPasivní: vždy se ptá „D“.\n"
            "Aktivní: nic neposílá, jen přijímá (tlačítko FUNCTION → COM).")
        self.cb_mode.currentIndexChanged.connect(self._apply_link_options)
        conn.addWidget(self.cb_mode)
        conn.addWidget(QLabel("Vzorkování:"))
        self.cb_interval = QComboBox()
        for label, s in INTERVALS:
            self.cb_interval.addItem(label, s)
        self.cb_interval.setToolTip("Nejkratší odstup dotazů v pasivním režimu.\n"
                                    "V aktivním režimu (COM) určuje tempo přístroj (~2,5/s).")
        self.cb_interval.currentIndexChanged.connect(self._apply_link_options)
        conn.addWidget(self.cb_interval)
        conn.addSpacing(10)
        self.btn_connect = QPushButton("Připojit", checkable=True, minimumWidth=110)
        self.btn_connect.toggled.connect(self.toggle_connect)
        conn.addWidget(self.btn_connect)
        self.lbl_state = QLabel()
        conn.addWidget(self.lbl_state, 1)
        root.addLayout(conn)

        # --- displej + statistiky ---
        top = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.lcd = LcdPanel()
        lv.addWidget(self.lcd, 1)
        self.lbl_note = QLabel(RANGE_NOTE, objectName="note", wordWrap=True)
        lv.addWidget(self.lbl_note)
        top.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self._build_stats())
        rv.addWidget(self._build_record())
        rv.addWidget(self._build_alarm())
        rv.addStretch(1)
        top.addWidget(right)
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 0)
        right.setMinimumWidth(420)
        right.setMaximumWidth(560)

        # --- graf ---
        graph = QWidget()
        gv = QVBoxLayout(graph)
        gv.setContentsMargins(0, 0, 0, 0)
        gbar = QHBoxLayout()
        gbar.addWidget(QLabel("Graf – zobrazit posledních:"))
        self.cb_span = QComboBox()
        for label, s in GRAPH_SPANS:
            self.cb_span.addItem(label, s)
        self.cb_span.currentIndexChanged.connect(lambda: self._update_plot(True))
        gbar.addWidget(self.cb_span)
        self.chk_follow = QCheckBox("Sledovat nová data", checked=True)
        self.chk_follow.setToolTip("Vypněte, chcete-li graf volně posouvat a "
                                   "zvětšovat myší (kolečko, tažení, pravé tlačítko).")
        self.chk_follow.toggled.connect(lambda: self._update_plot(True))
        gbar.addWidget(self.chk_follow)
        gbar.addStretch(1)
        b = QPushButton("Uložit graf jako PNG…")
        b.clicked.connect(self.export_png)
        gbar.addWidget(b)
        gv.addLayout(gbar)
        pg.setConfigOptions(antialias=True, background="#0e1216",
                            foreground="#8a949e")
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("left", "hodnota")
        self.plot.getPlotItem().setDownsampling(auto=True, mode="peak")
        self.plot.getPlotItem().setClipToView(True)
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#7fb2d9", width=1.6),
                                    connect="finite")
        self.ol_scatter = pg.ScatterPlotItem(size=7, symbol="t1",
                                             brush=pg.mkBrush("#e5484d"),
                                             pen=None, name="O.L")
        self.plot.addItem(self.ol_scatter)
        dash = pg.mkPen("#e5484d", width=1, style=Qt.DashLine)
        self.line_lo = pg.InfiniteLine(angle=0, pen=dash, movable=False)
        self.line_hi = pg.InfiniteLine(angle=0, pen=dash, movable=False)
        for ln in (self.line_lo, self.line_hi):
            ln.hide()
            self.plot.addItem(ln)
        gv.addWidget(self.plot, 1)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(top)
        vsplit.addWidget(graph)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        root.addWidget(vsplit, 1)
        self.setCentralWidget(central)

        # --- stavový řádek ---
        sb = QStatusBar()
        self.lbl_frames = QLabel()
        self.lbl_raw = QLabel()
        self.lbl_raw.setFont(QFont("Consolas", 9))
        sb.addWidget(self.lbl_frames)
        sb.addPermanentWidget(self.lbl_raw)
        self.setStatusBar(sb)

        # --- menu ---
        m = self.menuBar().addMenu("&Soubor")
        a = QAction("Uložit data aktuální řady do CSV…", self)
        a.setShortcut(QKeySequence.Save)
        a.triggered.connect(self.export_csv)
        m.addAction(a)
        a = QAction("Uložit graf jako PNG…", self)
        a.triggered.connect(self.export_png)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Konec", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)
        m = self.menuBar().addMenu("&Nápověda")
        a = QAction("O aplikaci", self)
        a.triggered.connect(self.about)
        m.addAction(a)

    def _kv(self, grid, row, col, key, cls="k"):
        lab = QLabel(key)
        lab.setProperty("class", cls)
        grid.addWidget(lab, row, col)
        return lab

    def _val(self, grid, row, col):
        lab = QLabel("—")
        lab.setProperty("class", "v")
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(lab, row, col)
        return lab

    def _build_stats(self) -> QGroupBox:
        box = QGroupBox("Statistika")
        g = QGridLayout(box)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(3)
        self._kv(g, 0, 1, "celá řada", "h").setAlignment(Qt.AlignRight)
        wbox = QHBoxLayout()
        wbox.addStretch(1)
        lab = QLabel("okno")
        lab.setProperty("class", "h")
        wbox.addWidget(lab)
        self.cb_window = QComboBox()
        for label, s in WINDOWS:
            self.cb_window.addItem(label, s)
        self.cb_window.setToolTip("Délka klouzavého okna pro průměr/min/max")
        self.cb_window.currentIndexChanged.connect(self._window_changed)
        wbox.addWidget(self.cb_window)
        g.addLayout(wbox, 0, 2)
        names = ["Průměr", "Minimum", "Maximum", "Rozpětí (max−min)",
                 "Směr. odchylka σ", "Vzorků", "Přetížení O.L"]
        self.st_run, self.st_win = {}, {}
        for i, n in enumerate(names, start=1):
            self._kv(g, i, 0, n)
            self.st_run[n] = self._val(g, i, 1)
            self.st_win[n] = self._val(g, i, 2)
        r = len(names) + 1
        self._kv(g, r, 0, "Doba řady")
        self.lbl_duration = self._val(g, r, 1)
        self._kv(g, r + 1, 0, "Vzorků za sekundu")
        self.lbl_rate = self._val(g, r + 1, 1)
        b = QPushButton("Vynulovat statistiku a graf")
        b.setMinimumHeight(30)
        b.setToolTip("Začne novou řadu měření. Probíhající záznam do CSV "
                     "pokračuje beze změny.")
        b.clicked.connect(lambda: self.new_series("vynulováno uživatelem"))
        g.addWidget(b, r + 2, 0, 1, 3)
        return box

    def _build_record(self) -> QGroupBox:
        box = QGroupBox("Záznam do CSV")
        v = QVBoxLayout(box)
        h = QHBoxLayout()
        self.btn_rec = QPushButton("●  Spustit záznam…", objectName="rec")
        self.btn_rec.clicked.connect(self.rec_start)
        self.btn_pause = QPushButton("Pozastavit", checkable=True, enabled=False)
        self.btn_pause.toggled.connect(self.rec_pause)
        self.btn_stop = QPushButton("Ukončit", enabled=False)
        self.btn_stop.clicked.connect(self.rec_stop)
        for w in (self.btn_rec, self.btn_pause, self.btn_stop):
            h.addWidget(w)
        v.addLayout(h)
        self.lbl_rec = QLabel("Záznam neběží.", wordWrap=True)
        self.lbl_rec.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_rec)
        self.chk_excel = QCheckBox("Formát pro český Excel (středník, desetinná čárka)")
        self.chk_excel.setToolTip("Vypnuto: čárka a desetinná tečka (Python, R, …)")
        v.addWidget(self.chk_excel)
        b = QPushButton("Uložit data aktuální řady do CSV…")
        b.setToolTip("Uloží vše, co je od začátku řady v paměti, i když záznam "
                     "neběžel.")
        b.clicked.connect(self.export_csv)
        v.addWidget(b)
        return box

    def _build_alarm(self) -> QGroupBox:
        box = QGroupBox("Hlídání mezí")
        g = QGridLayout(box)
        self.chk_alarm = QCheckBox("Hlídat")
        self.chk_alarm.toggled.connect(self._alarm_changed)
        g.addWidget(self.chk_alarm, 0, 0)
        self.chk_beep = QCheckBox("Pípnout")
        g.addWidget(self.chk_beep, 0, 1)
        self.lbl_alarm = QLabel("")
        g.addWidget(self.lbl_alarm, 0, 2, 1, 2)
        g.addWidget(QLabel("Dolní mez:"), 1, 0)
        self.ed_lo = QLineEdit(placeholderText="např. 4,5 nebo 15m")
        g.addWidget(self.ed_lo, 1, 1)
        g.addWidget(QLabel("Horní mez:"), 1, 2)
        self.ed_hi = QLineEdit(placeholderText="např. 5,5 nebo 1,2k")
        g.addWidget(self.ed_hi, 1, 3)
        for ed in (self.ed_lo, self.ed_hi):
            ed.editingFinished.connect(self._alarm_changed)
        self.lbl_alarm_unit = QLabel("Meze v základní jednotce (V, A, Ω, F, Hz); "
                                     "lze psát předpony m, µ/u, n, k, M.",
                                     wordWrap=True)
        self.lbl_alarm_unit.setProperty("class", "k")
        g.addWidget(self.lbl_alarm_unit, 2, 0, 1, 4)
        return box

    # ======================================================== nastavení
    def _load_settings(self, demo: bool, port: str | None = None) -> None:
        s = self.settings
        self.refresh_ports()
        want = SIM_PORT if demo else (port or s.value("port", ""))
        i = self.cb_port.findData(want)
        if i >= 0:
            self.cb_port.setCurrentIndex(i)
        self.cb_mode.setCurrentIndex(int(s.value("poll_mode", 0)))
        self.cb_interval.setCurrentIndex(int(s.value("interval", 0)))
        self.cb_window.setCurrentIndex(int(s.value("window", 2)))
        self.cb_span.setCurrentIndex(int(s.value("span", 2)))
        self.chk_excel.setChecked(s.value("excel_cz", "true") == "true")
        self.ed_lo.setText(s.value("alarm_lo", ""))
        self.ed_hi.setText(s.value("alarm_hi", ""))
        self.chk_beep.setChecked(s.value("beep", "false") == "true")
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1280, 860)
        self._render_idle()
        if demo or (i >= 0 and want):
            self.btn_connect.setChecked(True)

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("port", self.cb_port.currentData() or "")
        s.setValue("poll_mode", self.cb_mode.currentIndex())
        s.setValue("interval", self.cb_interval.currentIndex())
        s.setValue("window", self.cb_window.currentIndex())
        s.setValue("span", self.cb_span.currentIndex())
        s.setValue("excel_cz", "true" if self.chk_excel.isChecked() else "false")
        s.setValue("alarm_lo", self.ed_lo.text())
        s.setValue("alarm_hi", self.ed_hi.text())
        s.setValue("beep", "true" if self.chk_beep.isChecked() else "false")
        s.setValue("geometry", self.saveGeometry())

    # ========================================================= připojení
    def refresh_ports(self) -> None:
        cur = self.cb_port.currentData()
        self.cb_port.clear()
        for dev, desc in list_ports():
            self.cb_port.addItem(f"{dev} – {desc}", dev)
        self.cb_port.addItem("Simulátor (bez přístroje)", SIM_PORT)
        i = self.cb_port.findData(cur)
        if i >= 0:
            self.cb_port.setCurrentIndex(i)

    def toggle_connect(self, on: bool) -> None:
        if on:
            port = self.cb_port.currentData()
            if not port:
                self.btn_connect.setChecked(False)
                return
            self.link = MetexLink(
                port, self.cb_mode.currentData(), self.cb_interval.currentData(),
                on_reading=self.bridge.reading.emit,
                on_state=self.bridge.state.emit)
            self.link.start()
            self.btn_connect.setText("Odpojit")
            self.cb_port.setEnabled(False)
        else:
            if self.link:
                self.link.stop()
            self.link = None
            self.btn_connect.setText("Připojit")
            self.cb_port.setEnabled(True)
            self.on_state(LinkState.STOPPED, "")

    def _apply_link_options(self) -> None:
        if self.link:
            self.link.poll_mode = self.cb_mode.currentData()
            self.link.poll_interval = self.cb_interval.currentData()

    def on_state(self, state: LinkState, msg: str) -> None:
        col = STATE_COLORS.get(state, "#6b7680")
        text = state.value + (f" – {msg}" if msg and state != LinkState.RECEIVING
                              else "")
        if state == LinkState.NO_DATA and self.link and self.link.stats.frames_ok == 0:
            text += (" (zapnutý přístroj? kabel v boku přístroje? "
                     "převodník na správném COM?)")
        self.lbl_state.setText(f"<span style='color:{col}'>●</span> {text}")
        self.lcd.connected = state == LinkState.RECEIVING
        if state != LinkState.RECEIVING:
            self.lcd.com_active = False
        self.lcd.update()

    # ============================================================== data
    def on_reading(self, r: Reading) -> None:
        if self.quantity is not None and r.quantity != self.quantity:
            self.new_series(f"změna funkce na {r.mode_desc} [{r.unit.base}]")
        self.quantity = r.quantity
        self.last = r
        self.rx_times.append(r.t_mono)
        key = r.quantity
        if r.range_value is not None:
            self.last_range[key] = (r.range_value * r.unit.factor, r.range_label)
        rng_si, rng_label = self.last_range.get(key, (None, "?"))

        # statistika a graf
        if r.overload:
            self.run.overloads += 1
            self.win.add_overload(r.t_mono)
            self.ol_t.append(r.t_wall)
            self.ol_v.append(rng_si if rng_si is not None else
                             (max(self.v) if self.v else 0.0))
            self.t.append(r.t_wall)
            self.v.append(math.nan)
        elif r.value_si is not None:
            self.run.add(r.value_si)
            self.win.add(r.t_mono, r.value_si)
            self.t.append(r.t_wall)
            self.v.append(r.value_si)
        if len(self.t) > MAX_POINTS:
            del self.t[: len(self.t) - MAX_POINTS]
            del self.v[: len(self.v) - MAX_POINTS]

        alarm = self._check_alarm(r)
        self.records.append((r, alarm, rng_label))
        if self.logger and not self.rec_paused:
            try:
                self.logger.write(r, alarm, rng_label)
            except OSError as e:
                self.rec_stop()
                QMessageBox.critical(self, APP, f"Zápis do CSV selhal:\n{e}")

        # displej
        lcd = self.lcd
        lcd.connected = True
        lcd.value_text = r.text
        lcd.overload = r.overload
        lcd.mode_label = r.mode_label
        lcd.mode_desc = r.mode_desc
        lcd.unit_label = r.unit.label
        lcd.range_label = rng_label if rng_label != "?" else ""
        lcd.bar_fraction = (abs(r.value_si) / rng_si
                            if r.value_si is not None and rng_si else None)
        lcd.com_active = bool(self.link and self.link.stats.active_mode)
        self._update_notes(r)
        self.dirty = True

    def _update_notes(self, r: Reading) -> None:
        current = r.unit.base == "A"
        self.lbl_note.setObjectName("warn" if current else "note")
        self.lbl_note.setText(CURRENT_NOTE if current else RANGE_NOTE)
        self.lbl_note.style().unpolish(self.lbl_note)
        self.lbl_note.style().polish(self.lbl_note)
        self.lbl_alarm_unit.setText(
            f"Meze v jednotkách {r.unit.base or '(bez jednotky)'}; lze psát "
            "předpony m, µ/u, n, k, M (např. 15m = 0,015).")

    def new_series(self, why: str) -> None:
        self.run.reset()
        self.win.reset()
        self.t.clear()
        self.v.clear()
        self.ol_t.clear()
        self.ol_v.clear()
        self.records.clear()
        self.series_t0 = time.time()
        self.alarm_events = 0
        self.quantity = None
        self.statusBar().showMessage(f"Nová řada měření ({why}).", 8000)
        self.dirty = True
        self._update_plot(True)

    def _window_changed(self) -> None:
        sec = self.cb_window.currentData()
        old = list(self.win.buf)
        self.win = WindowStats(sec)
        for t, v in old:
            self.win.add(t, v)
        self.dirty = True

    # ============================================================ alarmy
    def _limits(self) -> tuple[float | None, float | None]:
        return parse_si(self.ed_lo.text()), parse_si(self.ed_hi.text())

    def _alarm_changed(self) -> None:
        lo, hi = self._limits()
        on = self.chk_alarm.isChecked()
        for ln, val in ((self.line_lo, lo), (self.line_hi, hi)):
            if on and val is not None:
                ln.setValue(val)
                ln.show()
            else:
                ln.hide()
        if not on:
            self.alarm_state = ""
            self.lcd.alarm = ""
            self.lbl_alarm.setText("")
            self.lcd.update()

    def _check_alarm(self, r: Reading) -> str:
        if not self.chk_alarm.isChecked():
            return ""
        lo, hi = self._limits()
        v = r.value_si
        state = ""
        if r.overload and hi is not None:
            state = "HIGH"
        elif v is not None:
            if hi is not None and v > hi:
                state = "HIGH"
            elif lo is not None and v < lo:
                state = "LOW"
        if state and state != self.alarm_state:
            self.alarm_events += 1
            if self.chk_beep.isChecked():
                QApplication.beep()
        self.alarm_state = state
        self.lcd.alarm = state
        txt = {"HIGH": "nad horní mezí", "LOW": "pod dolní mezí"}.get(state, "v mezích")
        col = "#e5484d" if state else "#3dbb6c"
        self.lbl_alarm.setText(f"<span style='color:{col}'>● {txt}</span> "
                               f"(překročení: {self.alarm_events}×)")
        return state

    # ============================================================ záznam
    def rec_start(self) -> None:
        d = self.settings.value("rec_dir", os.path.join(
            os.path.expanduser("~"), "Documents"))
        name = f"metex_{dt.datetime.now():%Y-%m-%d_%H%M%S}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Kam ukládat záznam",
                                              os.path.join(d, name),
                                              "CSV (*.csv)")
        if not path:
            return
        try:
            self.logger = CsvLogger(path, self.chk_excel.isChecked())
        except OSError as e:
            QMessageBox.critical(self, APP, f"Soubor nelze otevřít:\n{e}")
            return
        self.settings.setValue("rec_dir", os.path.dirname(path))
        self.rec_t0 = time.time()
        self.rec_paused = False
        self.btn_rec.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.chk_excel.setEnabled(False)
        self.lcd.rec = "REC"

    def rec_pause(self, on: bool) -> None:
        self.rec_paused = on
        self.btn_pause.setText("Pokračovat" if on else "Pozastavit")
        self.lcd.rec = "PAUSE" if on else "REC"
        self.lcd.update()

    def rec_stop(self) -> None:
        if self.logger:
            self.logger.close()
            self.statusBar().showMessage(
                f"Záznam uložen: {self.logger.path} ({self.logger.rows} řádků)", 15000)
        self.logger = None
        self.btn_pause.setChecked(False)
        self.btn_rec.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.chk_excel.setEnabled(True)
        self.lcd.rec = ""
        self.lcd.update()

    def export_csv(self) -> None:
        if not self.records:
            QMessageBox.information(self, APP, "Zatím nejsou žádná data.")
            return
        d = self.settings.value("rec_dir", os.path.expanduser("~/Documents"))
        name = f"metex_export_{dt.datetime.now():%Y-%m-%d_%H%M%S}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Uložit data řady",
                                              os.path.join(d, name), "CSV (*.csv)")
        if not path:
            return
        try:
            n = write_csv(path, list(self.records), self.chk_excel.isChecked())
        except OSError as e:
            QMessageBox.critical(self, APP, f"Uložení selhalo:\n{e}")
            return
        self.statusBar().showMessage(f"Uloženo {n} řádků do {path}", 10000)

    def export_png(self) -> None:
        from pyqtgraph.exporters import ImageExporter
        d = self.settings.value("rec_dir", os.path.expanduser("~/Documents"))
        name = f"metex_graf_{dt.datetime.now():%Y-%m-%d_%H%M%S}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Uložit graf",
                                              os.path.join(d, name), "PNG (*.png)")
        if path:
            ex = ImageExporter(self.plot.getPlotItem())
            ex.parameters()["width"] = 1800
            ex.export(path)
            self.statusBar().showMessage(f"Graf uložen: {path}", 10000)

    # ====================================================== periodicky
    def _blink(self) -> None:
        self.lcd.blink = not self.lcd.blink
        if self.lcd.overload or self.lcd.rec == "PAUSE":
            self.lcd.update()

    def _tick(self) -> None:
        link = self.link
        if link:
            st = link.stats
            self.lbl_frames.setText(
                f"rámce OK {st.frames_ok} · chybné {st.frames_bad} · "
                f"bez odpovědi {st.timeouts} · dotazů {st.requests} · "
                f"znovupřipojení {st.reconnects}"
                + (" · přístroj vysílá sám (COM)" if st.active_mode else ""))
            self.lbl_raw.setText(f"poslední rámec: {st.last_raw!r}"
                                 if st.last_raw else "")
            if self.lcd.com_active != st.active_mode:
                self.lcd.com_active = st.active_mode
                self.lcd.update()
        if self.logger:
            el = time.time() - self.rec_t0
            self.lbl_rec.setText(
                f"{'POZASTAVENO – ' if self.rec_paused else 'Zaznamenávám – '}"
                f"{self.logger.rows} řádků, {fmt_dur(el)}<br>"
                f"<span style='color:#8a949e'>{self.logger.path}</span>")
        elif not self.lbl_rec.text().startswith("Záznam neběží"):
            self.lbl_rec.setText("Záznam neběží.")
        if self.dirty:
            self.dirty = False
            self._update_stats()
            self._update_plot()
            self.lcd.update()

    def _fmt(self, x: float) -> str:
        r = self.last
        if r is None or x is None or (isinstance(x, float) and math.isnan(x)):
            return "—"
        f = r.unit.factor or 1.0
        return f"{format_number(x / f)} {r.unit.label}"

    def _update_stats(self) -> None:
        now = time.monotonic()
        for target, sm, ol in ((self.st_run, self.run.summary(), self.run.overloads),
                               (self.st_win, self.win.summary(now),
                                self.win.overload_count)):
            target["Průměr"].setText(self._fmt(sm.mean))
            target["Minimum"].setText(self._fmt(sm.min))
            target["Maximum"].setText(self._fmt(sm.max))
            target["Rozpětí (max−min)"].setText(
                self._fmt(sm.max - sm.min) if sm.n else "—")
            target["Směr. odchylka σ"].setText(self._fmt(sm.std))
            target["Vzorků"].setText(str(sm.n))
            target["Přetížení O.L"].setText(
                f"<span style='color:#e5484d'>{ol}</span>" if ol else "0")
        self.lbl_duration.setText(fmt_dur(time.time() - self.series_t0)
                                  if self.records else "—")
        if len(self.rx_times) >= 2:
            span = self.rx_times[-1] - self.rx_times[0]
            self.lbl_rate.setText(f"{(len(self.rx_times) - 1) / span:.2f}"
                                  if span > 0 else "—")
        # vedlejší displej: klouzavý průměr
        wsm = self.win.summary(now)
        r = self.last
        if r is not None and wsm.n:
            val = wsm.mean / (r.unit.factor or 1.0)
            self.lcd.sub_label = f"Ø {self.cb_window.currentText()}"
            self.lcd.sub_text = fit_digits(val, r.text)
            self.lcd.sub_unit = r.unit.label
        else:
            self.lcd.sub_text = ""

    def _update_plot(self, force_range: bool = False) -> None:
        if not self.t:
            self.curve.setData([], [])
            self.ol_scatter.setData([], [])
            return
        t = np.fromiter(self.t, float, len(self.t))
        v = np.fromiter(self.v, float, len(self.v))
        self.curve.setData(t, v, connect="finite")
        self.ol_scatter.setData(self.ol_t, self.ol_v)
        if self.last:
            self.plot.setLabel("left", self.last.mode_desc,
                               units=self.last.unit.base)
        if self.chk_follow.isChecked():
            span = self.cb_span.currentData()
            end = max(t[-1], time.time())
            start = end - span if span else t[0]
            self.plot.setXRange(start, end + (span or (end - t[0])) * 0.02,
                                padding=0)
            vi = v[(t >= start) & np.isfinite(v)]
            if vi.size:
                lo, hi = float(vi.min()), float(vi.max())
                lo_l, hi_l = self._limits()
                if self.chk_alarm.isChecked():
                    lo = min(x for x in (lo, lo_l) if x is not None)
                    hi = max(x for x in (hi, hi_l) if x is not None)
                pad = (hi - lo) * 0.08 or max(abs(hi) * 0.01, 1e-9)
                self.plot.setYRange(lo - pad, hi + pad, padding=0)

    # ========================================================== ostatní
    def _render_idle(self) -> None:
        self.lcd.value_text = "----"
        self.on_state(LinkState.STOPPED, "")

    def about(self) -> None:
        QMessageBox.about(self, APP, f"""<h3>{APP} v počítači</h3>
<p>Verze {__version__}. Čte multimetr Metex M-3650D (řada M-3600D) přes
RS-232 (1200 Bd, 7N2). Do přístroje posílá pouze dotaz „D“, nic nenastavuje.</p>
<p>Protokol ověřen podle návodu k přístroji, libsigrok (metex14) a QtDMM.</p>
<p>© 2026 jiri-lorenc. Svobodný software pod licencí
<a href="https://www.gnu.org/licenses/gpl-3.0.html">GNU GPL verze 3</a>:
smíte ho šířit a upravovat za jejích podmínek. Je poskytován
<b>bez jakékoli záruky</b>.</p>""")

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.logger:
            r = QMessageBox.question(self, APP, "Probíhá záznam. Ukončit ho a "
                                     "zavřít aplikaci?")
            if r != QMessageBox.Yes:
                ev.ignore()
                return
            self.rec_stop()
        self._save_settings()
        if self.link:
            self.link.stop()
        ev.accept()


def fmt_dur(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fit_digits(val: float, like: str) -> str:
    """Zformátuje číslo se stejným počtem desetinných míst jako displej."""
    dec = len(like.split(".", 1)[1]) if "." in like else 0
    s = f"{val:.{dec}f}"
    if len(s.lstrip("-").replace(".", "")) > 4:
        s = f"{val:.{max(dec - 1, 0)}f}"
    return s


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("Metex M-3650D")
    apply_dark_palette(app)
    port = argv[argv.index("--port") + 1] if "--port" in argv[:-1] else None
    w = MainWindow(demo="--demo" in argv, port=port)
    w.show()
    return app.exec()
