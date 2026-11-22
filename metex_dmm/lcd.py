"""Kreslený „LCD“ displej ve stylu Metexu: sedmisegmentové číslice,
symboly režimu, jednotka, bargraf 0–20 a malý vedlejší displej."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

# segmenty: a(nahoře) b(vpravo nahoře) c(vpravo dole) d(dole) e(vlevo dole)
# f(vlevo nahoře) g(uprostřed)
SEG = {
    "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
    "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "-": "g", " ": "", "O": "abcdef", "L": "def", "H": "bcefg", "i": "c",
    "o": "cdeg", "E": "adefg", "r": "eg", "n": "ceg", "P": "abefg",
    "A": "abcefg", "C": "adef", "F": "aefg", "U": "bcdef", "u": "cde",
    "d": "bcdeg", "b": "cdefg", "t": "defg", "S": "acdfg", "_": "d",
}

# střídmá „laboratorní“ paleta: tmavý displej, světlé segmenty
LCD_BG = QColor("#0e1216")
LCD_BORDER = QColor("#2a3138")
LCD_BORDER_ALARM = QColor("#e5484d")
SEG_ON = QColor("#eef2f5")
SEG_GHOST = QColor(255, 255, 255, 10)
SEG_OFF = QColor("#5b646d")        # odpojeno: šedé číslice
ACCENT = QColor("#7fb2d9")         # popisky režimu, stupnice
DIM = QColor("#8a949e")
REC = QColor("#e5484d")


def split_display(text: str, n_digits: int = 4) -> tuple[bool, list[tuple[str, bool]]]:
    """Rozloží text hodnoty na (záporné?, [(znak, tečka za ním)]) zprava
    zarovnané na n_digits pozic. Úvodní nuly před celou částí zhasne
    (jako displej přístroje), poslední nulu před tečkou nechá."""
    neg = text.startswith("-")
    t = text.lstrip("+-")
    cells: list[list] = []
    for ch in t:
        if ch == "." and cells:
            cells[-1][1] = True
        elif ch == ".":
            cells.append([" ", True])
        else:
            cells.append([ch, False])
    # zhasnout úvodní nuly
    for i, c in enumerate(cells):
        if c[0] == "0" and not c[1] and i + 1 < len(cells):
            c[0] = " "
        else:
            break
    cells = cells[-n_digits:]
    while len(cells) < n_digits:
        cells.insert(0, [" ", False])
    return neg, [(c[0], c[1]) for c in cells]


class LcdPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(420, 220)
        self.value_text = "----"
        self.mode_label = ""      # "DC", "AC", "Ω", "FREQ" …
        self.unit_label = ""
        self.range_label = ""
        self.mode_desc = ""
        self.bar_fraction: float | None = None   # 0..1 z rozsahu
        self.overload = False
        self.blink = False
        self.connected = False
        self.com_active = False
        self.rec = ""             # "", "REC", "PAUSE"
        self.alarm = ""           # "", "HIGH", "LOW"
        self.sub_label = ""       # vedlejší displej: popisek
        self.sub_text = ""        # vedlejší displej: hodnota
        self.sub_unit = ""
        self._on = SEG_ON

    # --- kreslení -------------------------------------------------------
    def _digit(self, p: QPainter, x: float, y: float, w: float, h: float,
               ch: str, dot: bool, skew: float = 0.12) -> None:
        t = max(w * 0.16, 2.0)       # tloušťka segmentu
        g = t * 0.18                 # mezera mezi segmenty
        hh = h / 2

        def pt(px, py):  # zkosení do kurzívy jako na LCD
            return QPointF(x + px + (h - py) * skew, y + py)

        def hseg(py):
            return QPolygonF([pt(t / 2 + g, py), pt(t + g, py - t / 2),
                              pt(w - t - g, py - t / 2), pt(w - t / 2 - g, py),
                              pt(w - t - g, py + t / 2), pt(t + g, py + t / 2)])

        def vseg(px, y0, y1):
            return QPolygonF([pt(px, y0 + g), pt(px + t / 2, y0 + t / 2 + g),
                              pt(px + t / 2, y1 - t / 2 - g), pt(px, y1 - g),
                              pt(px - t / 2, y1 - t / 2 - g),
                              pt(px - t / 2, y0 + t / 2 + g)])

        polys = {
            "a": hseg(t / 2), "g": hseg(hh), "d": hseg(h - t / 2),
            "f": vseg(t / 2, t / 2, hh), "b": vseg(w - t / 2, t / 2, hh),
            "e": vseg(t / 2, hh, h - t / 2), "c": vseg(w - t / 2, hh, h - t / 2),
        }
        on = SEG.get(ch, "")
        for name, poly in polys.items():
            p.setBrush(self._on if name in on else SEG_GHOST)
            p.drawPolygon(poly)
        r = t * 0.75
        p.setBrush(self._on if dot else SEG_GHOST)
        p.drawEllipse(QPointF(x + w + t * 0.9, y + h - r), r, r)

    def _text(self, p: QPainter, rect: QRectF, text: str, size: float,
              align=Qt.AlignLeft | Qt.AlignVCenter, bold=True,
              color: QColor | None = None) -> None:
        f = QFont("Segoe UI", 1)
        f.setPixelSize(max(int(size), 6))
        f.setBold(bold)
        p.setFont(f)
        p.setPen(color or self._on)
        p.drawText(rect, int(align), text)
        p.setPen(Qt.NoPen)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        self._on = SEG_ON if self.connected else SEG_OFF
        from PySide6.QtGui import QPen
        lcd = QRectF(1, 1, W - 2, H - 2)
        p.setBrush(LCD_BG)
        p.setPen(QPen(LCD_BORDER_ALARM if self.alarm else LCD_BORDER,
                      3 if self.alarm else 1))
        p.drawRoundedRect(lcd, 6, 6)
        p.setPen(Qt.NoPen)

        X, Y, LW, LH = lcd.x(), lcd.y(), lcd.width(), lcd.height()
        s = min(LW / 6.2, LH / 2.6)          # výška hlavních číslic
        # horní řádek symbolů
        top = QRectF(X + LW * 0.03, Y + LH * 0.02, LW * 0.94, LH * 0.13)
        syms = []
        if self.com_active:
            syms.append("COM")
        if self.rec:
            syms.append(self.rec)
        if self.alarm:
            syms.append("ALARM " + self.alarm)
        x = top.x()
        for sym in syms:
            col = REC if sym in ("REC",) or sym.startswith("ALARM") else ACCENT
            self._text(p, QRectF(x, top.y(), top.width(), top.height()), sym,
                       min(LH * 0.075, 20), color=col)
            x += (len(sym) + 2) * min(LH * 0.06, 16)
        self._text(p, top, self.mode_desc, min(LH * 0.07, 20),
                   Qt.AlignRight | Qt.AlignVCenter, bold=False, color=DIM)

        # vedlejší displej vpravo nahoře (jako FREQ na přístroji)
        if self.sub_text:
            sh = s * 0.32
            sw = sh * 0.55
            sx = X + LW * 0.60
            sy = Y + LH * 0.17
            self._text(p, QRectF(sx - LW * 0.17, sy, LW * 0.16, sh),
                       self.sub_label, sh * 0.42, Qt.AlignRight | Qt.AlignVCenter,
                       bold=False, color=DIM)
            neg, cells = split_display(self.sub_text)
            if neg:
                p.setBrush(self._on)
                p.drawRect(QRectF(sx - sw * 0.1, sy + sh / 2 - sh * 0.05,
                                  sw * 0.5, sh * 0.1))
            for i, (ch, dot) in enumerate(cells):
                self._digit(p, sx + sw * 0.5 + i * sw * 1.45, sy, sw, sh, ch, dot)
            self._text(p, QRectF(sx + sw * 0.5 + 4 * sw * 1.45, sy, LW * 0.15, sh),
                       self.sub_unit, sh * 0.5)

        # hlavní číslice
        dh = s
        dw = s * 0.55
        pitch = dw * 1.42
        total = 4 * pitch
        x0 = X + (LW - total) / 2 + LW * 0.02
        y0 = Y + LH * 0.30
        # režim vlevo
        self._text(p, QRectF(X + LW * 0.02, y0, x0 - X - LW * 0.08, dh),
                   self.mode_label, dh * 0.24, Qt.AlignCenter, color=ACCENT)
        text = self.value_text if not self.overload else "O.L"
        neg, cells = split_display(text)
        if neg:
            p.setBrush(self._on)
            p.drawRect(QRectF(x0 - dw * 0.75, y0 + dh / 2 - dh * 0.045,
                              dw * 0.55, dh * 0.09))
        for i, (ch, dot) in enumerate(cells):
            self._digit(p, x0 + i * pitch, y0, dw, dh, ch, dot)
        # jednotka vpravo
        ux = x0 + total + dw * 0.2
        self._text(p, QRectF(ux, y0 + dh * 0.45, X + LW - ux, dh * 0.55),
                   self.unit_label, dh * 0.3)

        # bargraf 0–20 (2000 bodů = 20 dílků po 100)
        by = y0 + dh + LH * 0.07
        bx0 = X + LW * 0.08
        bx1 = X + LW * 0.92
        bh = LH * 0.045
        nseg = 42
        sw_ = (bx1 - bx0) / nseg
        frac = self.bar_fraction
        lit = 0
        if self.overload:
            lit = nseg if self.blink else 0
        elif frac is not None:
            lit = int(round(min(max(frac, 0.0), 1.05) * 40))
        for i in range(nseg):
            p.setBrush(self._on if i < lit else SEG_GHOST)
            p.drawRect(QRectF(bx0 + i * sw_ + sw_ * 0.15, by, sw_ * 0.7, bh))
        # stupnice
        for k, lab in enumerate(("0", "5", "10", "15", "20")):
            xx = bx0 + k * 10 * sw_ + sw_ / 2
            p.setBrush(DIM)
            p.drawRect(QRectF(xx - 0.5, by + bh + 2, 1.0, bh * 0.45))
            self._text(p, QRectF(xx - 20, by + bh * 1.5, 40, bh * 1.3), lab,
                       min(bh * 0.85, 16), Qt.AlignCenter, bold=False,
                       color=DIM)
        # rozsah
        self._text(p, QRectF(X + LW * 0.03, Y + LH * 0.86, LW * 0.94, LH * 0.12),
                   ("Rozsah " + self.range_label) if self.range_label else "",
                   min(LH * 0.07, 20), Qt.AlignRight | Qt.AlignVCenter,
                   bold=False, color=DIM)
        p.end()
