"""Visual language: neon palette, glow helpers, one shared animation clock."""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QEasingCurve, QObject, QPropertyAnimation, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainterPath, QPen

from . import i18n

# ---- palette
BG = QColor(7, 7, 16)
CYAN = QColor(34, 211, 238)
VIOLET = QColor(139, 92, 246)
PINK = QColor(236, 72, 153)
BLUE = QColor(59, 130, 246)
GREEN = QColor(52, 211, 153)
AMBER = QColor(251, 191, 36)
RED = QColor(251, 113, 133)
TEXT = QColor(238, 242, 255)
MUTED = QColor(150, 158, 190)


def rgba(c: QColor, a: float) -> QColor:
    q = QColor(c)
    q.setAlphaF(max(0.0, min(1.0, a)))
    return q


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


# ---- fonts
FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Tahoma", "Inter", "Noto Sans", "DejaVu Sans", "Arial"]


def font(px: float, bold: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(FAMILIES)
    f.setPixelSize(int(px))
    f.setBold(bold)
    # letter-spacing breaks Arabic/Persian letter joining, so only use it for Latin text
    if spacing and i18n.LANG == "en":
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


# ---- shared clock (a single 60fps timer drives the whole UI)
class Clock(QObject):
    tick = Signal()

    def __init__(self):
        super().__init__()
        self._el = QElapsedTimer()
        self._el.start()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self.tick.emit)

    def start(self):
        self._timer.start()

    @property
    def t(self) -> float:
        return self._el.elapsed() / 1000.0


_clock: Clock | None = None


def clock() -> Clock:
    global _clock
    if _clock is None:
        _clock = Clock()
        _clock.start()
    return _clock


# ---- animation helper for Qt Properties
def animate(obj, prop: str, end, ms: int = 300, curve=QEasingCurve.OutCubic):
    store = obj.__dict__.setdefault("_anims", {})
    a = store.get(prop)
    if a is None:
        a = QPropertyAnimation(obj, prop.encode(), obj)
        store[prop] = a
    a.stop()
    a.setDuration(ms)
    a.setEasingCurve(curve)
    a.setStartValue(obj.property(prop))
    a.setEndValue(end)
    a.start()


# ---- painting helpers
def draw_glow(p, rect: QRectF, radius: float, color: QColor, strength: float = 1.0, spread: int = 10):
    """Soft neon glow around a rounded rect (stacked translucent strokes)."""
    if strength <= 0.01:
        return
    p.save()
    p.setBrush(Qt.NoBrush)
    step = 2 if spread >= 6 else 1          # half the layers, compensated alpha -> same look, ~2x faster
    for i in range(spread, 0, -step):
        f = 1.0 - i / (spread + 1.0)
        pen = QPen(rgba(color, strength * 0.07 * step * f * f), i * 2)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawRoundedRect(rect, radius + i * 0.6, radius + i * 0.6)
    p.restore()


def draw_bar(p, r: QRectF, frac: float, t: float, c1: QColor, c2: QColor, glow: float = 1.0):
    """Glowing progress bar with a travelling shimmer."""
    h = r.height()
    rad = h / 2
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 20))
    p.drawRoundedRect(r, rad, rad)
    if frac <= 0.002:
        return
    fr = QRectF(r.left(), r.top(), max(h, r.width() * frac), h)
    draw_glow(p, fr, rad, mix(c1, c2, 0.5), 0.9 * glow, 6)
    g = QLinearGradient(fr.left(), 0, fr.right(), 0)
    g.setColorAt(0, c1)
    g.setColorAt(1, c2)
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(fr, rad, rad)
    p.save()
    path = QPainterPath()
    path.addRoundedRect(fr, rad, rad)
    p.setClipPath(path)
    x = fr.left() + ((t * 0.8) % 1.5 - 0.25) * fr.width()
    sg = QLinearGradient(x - 28, 0, x + 28, 0)
    sg.setColorAt(0, QColor(255, 255, 255, 0))
    sg.setColorAt(0.5, QColor(255, 255, 255, 120))
    sg.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(fr, sg)
    p.restore()


def draw_logo(p, r: QRectF, t: float = 0.0):
    """The VidShrink mark: two chevrons that 'compress' towards each other."""
    rr = r.height() * 0.30
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, CYAN)
    g.setColorAt(1, VIOLET)
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, rr, rr)
    cx, cy = r.center().x(), r.center().y()
    s = r.height() * 0.19
    gap = r.height() * (0.07 + 0.045 * math.sin(t * 3.2))
    pen = QPen(QColor(255, 255, 255), r.height() * 0.095, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    left = QPainterPath()
    left.moveTo(cx - gap - s, cy - s)
    left.lineTo(cx - gap, cy)
    left.lineTo(cx - gap - s, cy + s)
    right = QPainterPath()
    right.moveTo(cx + gap + s, cy - s)
    right.lineTo(cx + gap, cy)
    right.lineTo(cx + gap + s, cy + s)
    p.drawPath(left)
    p.drawPath(right)
