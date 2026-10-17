"""VidShrink – main window and application logic."""
from __future__ import annotations

import math
import os
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QLocale, QPointF, QRectF, QSettings, QSize, Qt, QUrl, QVariantAnimation
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QDesktopServices, QIcon, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget)

from . import __version__, i18n
from .engine import (Job, Options, VIDEO_EXTS, Worker, find_ffmpeg, human_size, probe)
from .i18n import tr
from .theme import (AMBER, BG, BLUE, CYAN, GREEN, MUTED, PINK, RED, TEXT, VIOLET, clamp, clock, draw_glow,
                    draw_logo, font, mix, rgba)
from .widgets import (DONE, FAILED, PROCESSING, QUEUED, SKIPPED, DropZone, FxOverlay, GlassPanel, GlowButton,
                      IconButton, QueueItem, Segmented, SLabel, StatsWidget, ToggleRow)

M = 18          # outer margin used for the window glow
R = 26          # window corner radius
VIDEO_FILTER = "Videos (" + " ".join(f"*{e}" for e in sorted(VIDEO_EXTS)) + ");;All files (*.*)"


def make_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHints(QPainter.Antialiasing)
        draw_logo(p, QRectF(s * 0.06, s * 0.06, s * 0.88, s * 0.88), 0.0)
        p.end()
        icon.addPixmap(pm)
    return icon


# =========================================================================== title bar
class TitleBar(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.setFixedHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 18, 0)
        lay.setSpacing(4)
        lay.addStretch(1)
        self.btn_lang = IconButton("lang")
        self.btn_min = IconButton("min")
        self.btn_close = IconButton("close")
        for b in (self.btn_lang, self.btn_min, self.btn_close):
            lay.addWidget(b)
        self.btn_min.clicked.connect(win.showMinimized)
        self.btn_close.clicked.connect(win.close)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.win.windowHandle():
            self.win.windowHandle().startSystemMove()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        t = clock().t
        lr = QRectF(30, 12, 40, 40)
        draw_glow(p, lr, 13, mix(CYAN, VIOLET, 0.5 + 0.5 * math.sin(t)), 0.9 + 0.3 * math.sin(t * 2), 12)
        draw_logo(p, lr, t)

        # animated gradient wordmark
        path = QPainterPath()
        f = font(26, True)
        path.addText(84, 40, f, "VidShrink")
        g = QLinearGradient(84 + 60 * math.sin(t * 0.8) - 60, 0, 84 + 160 + 60 * math.sin(t * 0.8), 0)
        g.setSpread(QLinearGradient.ReflectSpread)
        g.setColorAt(0, QColor(255, 255, 255))
        g.setColorAt(0.5, CYAN)
        g.setColorAt(1, VIOLET)
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawPath(path)
        p.setPen(rgba(MUTED, 0.95))
        p.setFont(font(10.5, True, 2.4))
        p.drawText(QRectF(86, 41, 300, 16), Qt.AlignLeft | Qt.AlignVCenter, tr("tagline"))
        p.setPen(rgba(MUTED, 0.55))
        p.setFont(font(10.5, True))
        wm = path.boundingRect().right() + 8
        p.drawText(QRectF(wm, 18, 80, 16), Qt.AlignLeft | Qt.AlignVCenter, f"v{__version__}")


# =========================================================================== main window
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("VidShrink", "VidShrink")
        lang = self.settings.value("lang", "")
        if not lang:
            lang = "fa" if QLocale.system().name().startswith("fa") else "en"
        i18n.set_lang(str(lang))

        self.setWindowTitle("VidShrink")
        self.setWindowIcon(make_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window | Qt.WindowMinimizeButtonHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setFixedSize(1120, 740)

        self.ffmpeg = find_ffmpeg()
        self.items: list[QueueItem] = []
        self.worker: Worker | None = None
        self.jobs_map: dict[int, QueueItem] = {}
        self.outdir = str(self.settings.value("outdir", "") or "")
        self.last_dir = ""
        self._img: QImage | None = None
        self._glow_pm: QPixmap | None = None
        self._corner_path: QPainterPath | None = None
        self._corner_rect = QRectF()
        self._skip, self._n, self._avg = 1, 0, 4.0
        self._bg: QImage | None = None
        self._bg_t = -1.0
        self._run_total = 0
        self._run_done = 0
        rnd = random.Random(7)
        self._stars = [(rnd.random(), rnd.random(), rnd.uniform(0.012, 0.04), rnd.uniform(0.8, 2.2),
                        rnd.uniform(0, 6.28)) for _ in range(46)]

        self._build_ui()
        self._load_settings()
        self.retranslate()
        clock().tick.connect(self._on_tick)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(M, M, M, M)
        root.setSpacing(0)

        self.titlebar = TitleBar(self)
        self.titlebar.btn_lang.clicked.connect(self._toggle_lang)
        root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(26, 0, 26, 0)
        body.setSpacing(20)
        root.addLayout(body, 1)

        # ---- left column
        self.left = QWidget()
        lv = QVBoxLayout(self.left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        self.drop = DropZone()
        self.drop.clicked.connect(self.browse)
        lv.addWidget(self.drop, 1)

        self.q_head = QWidget()
        qh = QHBoxLayout(self.q_head)
        qh.setContentsMargins(6, 2, 0, 0)
        self.q_title = SLabel("queue")
        qh.addWidget(self.q_title)
        qh.addStretch(1)
        self.btn_clear = GlowButton("clear", "ghost", small=True)
        self.btn_clear.setFixedWidth(150)
        self.btn_clear.clicked.connect(self.clear_all)
        qh.addWidget(self.btn_clear)
        lv.addWidget(self.q_head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:6px 0;}"
            "QScrollBar::handle:vertical{background:rgba(139,92,246,150);border-radius:4px;min-height:30px;}"
            "QScrollBar::handle:vertical:hover{background:rgba(34,211,238,210);}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")
        self.scroll.viewport().setAutoFillBackground(False)
        self.list_w = QWidget()
        self.list_w.setAutoFillBackground(False)
        self.list_lay = QVBoxLayout(self.list_w)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(2)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_w)
        self.list_w.setAutoFillBackground(False)      # setWidget() turns it back on
        self.scroll.viewport().setAutoFillBackground(False)
        lv.addWidget(self.scroll, 3)
        body.addWidget(self.left, 1)

        # ---- right column (settings)
        panel = GlassPanel()
        panel.setFixedWidth(336)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(20, 18, 20, 16)
        pv.setSpacing(2)

        self.lbl_quality = SLabel("quality")
        self.seg_preset = Segmented(["p_tiny", "p_bal", "p_high"])
        self.lbl_pdesc = QLabel()
        self.lbl_pdesc.setWordWrap(True)
        self.lbl_pdesc.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lbl_pdesc.setFixedHeight(36)
        self.lbl_pdesc.setStyleSheet("background:transparent;color:rgba(150,158,190,235);")
        self.lbl_pdesc.setFont(font(12))
        self.lbl_codec = SLabel("codec")
        self.seg_codec = Segmented(["c_h265", "c_h264"])
        self.lbl_res = SLabel("res")
        self.seg_res = Segmented(["r_orig", "1080p", "720p", "480p"])
        self.tg_gpu = ToggleRow("gpu", "gpu_hint")
        self.tg_mute = ToggleRow("mute", "mute_hint")
        self.lbl_out = SLabel("out")
        out_row = QHBoxLayout()
        out_row.setContentsMargins(0, 0, 0, 0)
        out_row.setSpacing(2)
        self.btn_out = GlowButton("", "ghost", small=True)
        self.btn_out.clicked.connect(self.choose_outdir)
        self.btn_reset = IconButton("reset")
        self.btn_reset.clicked.connect(self.reset_outdir)
        out_row.addWidget(self.btn_out, 1)
        out_row.addWidget(self.btn_reset)

        for w in (self.lbl_quality,):
            pv.addWidget(w)
        pv.addWidget(self.seg_preset)
        pv.addWidget(self.lbl_pdesc)
        pv.addSpacing(6)
        pv.addWidget(self.lbl_codec)
        pv.addWidget(self.seg_codec)
        pv.addSpacing(6)
        pv.addWidget(self.lbl_res)
        pv.addWidget(self.seg_res)
        pv.addSpacing(8)
        pv.addWidget(self.tg_gpu)
        pv.addWidget(self.tg_mute)
        pv.addSpacing(6)
        pv.addWidget(self.lbl_out)
        pv.addLayout(out_row)
        pv.addStretch(1)
        body.addWidget(panel)

        self.seg_preset.changed.connect(lambda i: (self._update_pdesc(), self._save()))
        self.seg_codec.changed.connect(lambda i: self._save())
        self.seg_res.changed.connect(lambda i: self._save())
        self.tg_gpu.toggled.connect(lambda v: self._save())
        self.tg_mute.toggled.connect(lambda v: self._save())

        # ---- bottom bar
        bottom = QHBoxLayout()
        bottom.setContentsMargins(26, 14, 26, 18)
        bottom.setSpacing(14)
        self.stats = StatsWidget()
        bottom.addWidget(self.stats, 1)
        self.btn_folder = GlowButton("open_folder", "ghost", small=True)
        self.btn_folder.setFixedWidth(170)
        self.btn_folder.setEnabled(False)
        self.btn_folder.clicked.connect(self.open_folder)
        bottom.addWidget(self.btn_folder, 0, Qt.AlignVCenter)
        self.btn_start = GlowButton("start", "primary")
        self.btn_start.setFixedWidth(280)
        self.btn_start.clicked.connect(self.on_start)
        bottom.addWidget(self.btn_start, 0, Qt.AlignVCenter)
        root.addLayout(bottom)

        self.fx = FxOverlay(self)
        self.fx.setGeometry(self.rect())
        self.fx.raise_()
        self._set_empty_state(animated=False)

    # ------------------------------------------------------------------ settings / i18n
    def _load_settings(self):
        s = self.settings
        self.seg_preset.setIndex(int(s.value("preset", 1)), False)
        self.seg_codec.setIndex(int(s.value("codec", 0)), False)
        self.seg_res.setIndex(int(s.value("res", 0)), False)
        self.tg_gpu.setChecked(str(s.value("gpu", "false")) == "true", False)
        self.tg_mute.setChecked(str(s.value("mute", "false")) == "true", False)

    def _save(self):
        s = self.settings
        s.setValue("preset", self.seg_preset.index())
        s.setValue("codec", self.seg_codec.index())
        s.setValue("res", self.seg_res.index())
        s.setValue("gpu", "true" if self.tg_gpu.state else "false")
        s.setValue("mute", "true" if self.tg_mute.state else "false")
        s.setValue("outdir", self.outdir)
        s.setValue("lang", i18n.LANG)

    def _update_pdesc(self):
        self.lbl_pdesc.setText(tr(("pd_tiny", "pd_bal", "pd_high")[self.seg_preset.index()]))

    def _toggle_lang(self):
        i18n.set_lang("fa" if i18n.LANG == "en" else "en")
        self._save()
        self.retranslate()

    def retranslate(self):
        self.titlebar.btn_lang.label = "FA" if i18n.LANG == "en" else "EN"
        for w in (self.q_title, self.lbl_quality, self.lbl_codec, self.lbl_res, self.lbl_out):
            w.retranslate()
        self.lbl_pdesc.setFont(font(12))
        self._update_pdesc()
        self._refresh_out_btn()
        self.update()

    def _refresh_out_btn(self):
        self.btn_out.setLabelFn(lambda: ("📁  " + (Path(self.outdir).name or self.outdir)) if self.outdir
                                else "📁  " + tr("out_same"))
        self.btn_reset.setVisible(bool(self.outdir))

    # ------------------------------------------------------------------ aurora background
    def _on_tick(self):
        """Adaptive frame rate: 60 fps on fast machines, 30/20 fps if painting gets expensive."""
        if self.isMinimized() or not self.isVisible():       # no CPU burn while minimized
            return
        self._n += 1
        if self._n % self._skip:
            return
        t0 = time.perf_counter()
        self.repaint()
        ms = (time.perf_counter() - t0) * 1000
        self._avg += (ms - self._avg) * 0.12
        self._skip = 1 if self._avg < 7 else 2 if self._avg < 15 else 3

    def resizeEvent(self, e):
        self.fx.setGeometry(self.rect())
        self._glow_pm = None
        super().resizeEvent(e)

    def _make_glow_pixmap(self) -> QPixmap:
        pm = QPixmap(self.size())
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHints(QPainter.Antialiasing)
        inner = QRectF(self.rect()).adjusted(M, M, -M, -M)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, CYAN)
        g.setColorAt(0.5, VIOLET)
        g.setColorAt(1, PINK)
        for i in range(M, 0, -1):
            f = 1 - i / (M + 1.0)
            pen = QPen(QBrush(g), i * 2)
            op = 0.11 * f * f
            p.setOpacity(op)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(inner, R + i * 0.5, R + i * 0.5)
        p.end()
        return pm

    def _aurora(self, w: int, h: int, t: float) -> QImage:
        s = 0.22
        iw, ih = max(4, int(w * s)), max(4, int(h * s))
        if self._img is None or self._img.size() != QSize(iw, ih):
            self._img = QImage(iw, ih, QImage.Format_ARGB32_Premultiplied)
        img = self._img
        img.fill(BG)
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing)
        blobs = (
            (CYAN, 0.22, 0.22, 0.55, 0.13, 0.0),
            (VIOLET, 0.78, 0.25, 0.62, 0.10, 1.7),
            (PINK, 0.62, 0.88, 0.55, 0.12, 3.1),
            (BLUE, 0.12, 0.85, 0.50, 0.09, 4.6),
        )
        for col, cx, cy, rf, sp, ph in blobs:
            x = (cx + 0.20 * math.sin(t * sp * 2 + ph)) * iw
            y = (cy + 0.18 * math.cos(t * sp * 2.6 + ph * 1.7)) * ih
            r = rf * max(iw, ih)
            g = QRadialGradient(x, y, r)
            g.setColorAt(0, rgba(col, 0.46))
            g.setColorAt(0.55, rgba(col, 0.15))
            g.setColorAt(1, rgba(col, 0.0))
            q.setPen(Qt.NoPen)
            q.setBrush(g)
            q.drawEllipse(QPointF(x, y), r, r)
        q.end()
        return img

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        t = clock().t
        inner = QRectF(self.rect()).adjusted(M, M, -M, -M)

        # 1) aurora: rendered small + smoothly upscaled (free blur). The blobs drift slowly,
        #    so the expensive upscale is cached and refreshed ~20x per second only.
        if self._bg is None or self._bg.size() != inner.size().toSize() or t - self._bg_t > 0.045:
            if self._bg is None or self._bg.size() != inner.size().toSize():
                self._bg = QImage(inner.size().toSize(), QImage.Format_ARGB32_Premultiplied)
            bq = QPainter(self._bg)
            bq.setRenderHint(QPainter.SmoothPixmapTransform)
            bq.drawImage(QRectF(self._bg.rect()), self._aurora(self._bg.width(), self._bg.height(), t))
            bq.fillRect(self._bg.rect(), QColor(6, 6, 14, 118))      # keep text readable
            bq.end()
            self._bg_t = t
        p.drawImage(inner.topLeft(), self._bg)

        # 2) twinkling particles drifting upwards
        p.setPen(Qt.NoPen)
        for x, y, spd, size, ph in self._stars:
            yy = (y - t * spd) % 1.0
            xx = x + 0.012 * math.sin(t * 0.6 + ph)
            a = 0.18 + 0.42 * (0.5 + 0.5 * math.sin(t * 1.7 + ph))
            p.setBrush(QColor(255, 255, 255, int(255 * a)))
            p.drawEllipse(QPointF(inner.left() + xx * inner.width(), inner.top() + yy * inner.height()), size, size)

        # 3) round the corners by clearing the 4 corner slivers (much cheaper than an AA clip path)
        if self._corner_path is None or self._corner_rect != inner:
            cp = QPainterPath()
            cp.addRect(inner.adjusted(-2, -2, 2, 2))
            rr = QPainterPath()
            rr.addRoundedRect(inner, R, R)
            self._corner_path = cp.subtracted(rr)
            self._corner_rect = QRectF(inner)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillPath(self._corner_path, Qt.black)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # 4) pulsing neon halo (cached pixmap) + animated border
        if self._glow_pm is None:
            self._glow_pm = self._make_glow_pixmap()
        p.setOpacity(0.7 + 0.3 * math.sin(t * 1.6))
        p.drawPixmap(0, 0, self._glow_pm)
        p.setOpacity(1.0)
        cg = QConicalGradient(inner.center(), -(t * 30) % 360)
        for pos, c in ((0.0, CYAN), (0.25, VIOLET), (0.5, PINK), (0.75, VIOLET), (1.0, CYAN)):
            cg.setColorAt(pos, rgba(c, 0.85))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(cg), 1.6))
        p.drawRoundedRect(inner, R, R)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.drop.set_active(True)

    def dragLeaveEvent(self, e):
        self.drop.set_active(False)

    def dropEvent(self, e):
        self.drop.set_active(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.add_files(paths)
        e.acceptProposedAction()

    def browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, tr("dialog"), self.settings.value("lastdir", ""), VIDEO_FILTER)
        if files:
            self.settings.setValue("lastdir", os.path.dirname(files[0]))
            self.add_files(files)

    # ------------------------------------------------------------------ queue management
    def add_files(self, paths: list[str]):
        if not self.ffmpeg:
            self.fx.show_toast(tr("no_ffmpeg"), RED)
            return
        found: list[str] = []
        for p in paths:
            if os.path.isdir(p):
                for root, _, fs in os.walk(p):
                    for f in sorted(fs):
                        if Path(f).suffix.lower() in VIDEO_EXTS:
                            found.append(os.path.join(root, f))
            elif Path(p).suffix.lower() in VIDEO_EXTS and os.path.isfile(p):
                found.append(p)
        have = {it.path for it in self.items}
        bad = 0
        was_empty = not self.items
        for f in found:
            if f in have:
                continue
            info = probe(self.ffmpeg, f)
            if not info:
                bad += 1
                continue
            have.add(f)
            it = QueueItem(f, info)
            it.removable = self.worker is None
            it.removeRequested.connect(self.remove_item)
            self.list_lay.insertWidget(self.list_lay.count() - 1, it)
            self.items.append(it)
            QApplication.processEvents()
        if bad:
            self.fx.show_toast(tr("bad_files", n=bad), AMBER)
        if was_empty and self.items:
            self._set_has_items()
        self._refresh_stats()

    def remove_item(self, it: QueueItem):
        if it in self.items and self.worker is None:
            self.items.remove(it)
            it.setParent(None)
            it.deleteLater()
            if not self.items:
                self._set_empty_state(animated=True)
            self._refresh_stats()

    def clear_all(self):
        if self.worker is not None:
            return
        for it in self.items:
            it.setParent(None)
            it.deleteLater()
        self.items.clear()
        self._set_empty_state(animated=True)
        self._refresh_stats()

    # -- the drop zone morphs between a big hero and a compact bar
    def _anim_drop_height(self, a: int, b: int, on_done=None):
        anim = QVariantAnimation(self)
        anim.setDuration(520)
        anim.setStartValue(a)
        anim.setEndValue(b)
        from PySide6.QtCore import QEasingCurve
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.valueChanged.connect(lambda v: self.drop.setFixedHeight(int(v)))
        if on_done:
            anim.finished.connect(on_done)
        anim.start(QVariantAnimation.DeleteWhenStopped)
        self._drop_anim = anim

    def _set_has_items(self):
        self.q_head.setVisible(True)
        self.scroll.setVisible(True)
        self._anim_drop_height(max(self.drop.height(), 130), 130)

    def _set_empty_state(self, animated: bool):
        self.stats.set_overall(0.0, 500)
        self.q_head.setVisible(False)
        self.scroll.setVisible(False)

        def free():
            self.drop.setMinimumHeight(112)
            self.drop.setMaximumHeight(16777215)

        if animated:
            self._anim_drop_height(self.drop.height(), self.left.height(), free)
        else:
            free()

    # ------------------------------------------------------------------ options / output
    def choose_outdir(self):
        d = QFileDialog.getExistingDirectory(self, tr("dialog_out"), self.outdir or "")
        if d:
            self.outdir = d
            self._save()
            self._refresh_out_btn()

    def reset_outdir(self):
        self.outdir = ""
        self._save()
        self._refresh_out_btn()

    def current_options(self) -> Options:
        return Options(preset=self.seg_preset.index(), codec=self.seg_codec.index(), res=self.seg_res.index(),
                       gpu=self.tg_gpu.state, mute=self.tg_mute.state)

    @staticmethod
    def _unique(dst: Path, used: set) -> Path:
        n, cand = 2, dst
        while cand.exists() or str(cand) in used:
            cand = dst.with_name(f"{dst.stem}_{n}{dst.suffix}")
            n += 1
        used.add(str(cand))
        return cand

    # ------------------------------------------------------------------ run
    def on_start(self):
        if self.worker is not None:
            self.btn_start.setKey("stopping")
            self.btn_start.setEnabled(False)
            self.worker.cancel()
            return
        todo = [it for it in self.items if it.status in (QUEUED, FAILED)]
        if not todo:
            self.fx.show_toast(tr("nothing"), AMBER)
            return
        if not self.ffmpeg:
            self.fx.show_toast(tr("no_ffmpeg"), RED)
            return
        used: set[str] = set()
        jobs: list[Job] = []
        self.jobs_map = {}
        for i, it in enumerate(todo):
            folder = Path(self.outdir) if self.outdir else Path(it.path).parent
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            dst = self._unique(folder / f"{Path(it.path).stem}_vidshrink.mp4", used)
            jobs.append(Job(i, it.path, str(dst), it.info))
            self.jobs_map[i] = it
            it.reset()
        for it in self.items:
            it.removable = False
        self._run_total, self._run_done = len(jobs), 0
        self.stats.set_overall(0.0, 200)
        self.worker = Worker(self.ffmpeg, jobs, self.current_options(), self)
        self.worker.started_file.connect(lambda j: self.jobs_map[j].set_processing())
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_file.connect(self._on_file_done)
        self.worker.failed_file.connect(self._on_file_failed)
        self.worker.gpu_fallback.connect(lambda j: self.fx.show_toast(tr("gpu_fallback"), AMBER))
        self.worker.all_done.connect(self._on_all_done)
        self.btn_start.setKey("stop")
        self.btn_start.setVariant("danger")
        self.btn_folder.setEnabled(False)
        self.worker.start()

    def _on_progress(self, jid, frac, speed, eta):
        it = self.jobs_map.get(jid)
        if it:
            it.set_progress(frac, speed, eta)
        self.stats.set_overall((self._run_done + frac) / max(1, self._run_total), 180)

    def _on_file_done(self, jid, out_path, in_size, out_size):
        it = self.jobs_map[jid]
        it.set_done(out_path, in_size, out_size)
        if out_path:
            self.last_dir = os.path.dirname(out_path)
        self._run_done += 1
        self.stats.set_overall(self._run_done / max(1, self._run_total), 400)
        self._refresh_stats()

    def _on_file_failed(self, jid, msg):
        self.jobs_map[jid].set_failed(msg)
        self._run_done += 1
        self.stats.set_overall(self._run_done / max(1, self._run_total), 400)
        self._refresh_stats()

    def _on_all_done(self, cancelled: bool):
        w, self.worker = self.worker, None
        if w:
            w.wait(2000)
            w.deleteLater()
        for it in self.items:
            it.removable = True
            if it.status == PROCESSING:
                it.reset()
        self.btn_start.setEnabled(True)
        self.btn_start.setKey("start")
        self.btn_start.setVariant("primary")
        self.btn_folder.setEnabled(bool(self.last_dir))
        if cancelled:
            self.stats.set_overall(0.0, 300)
            self.fx.show_toast(tr("stopped"), AMBER)
            return
        saved = sum(max(0, it.in_size - it.out_size) for it in self.items if it.status == DONE)
        if any(it.status == DONE for it in self.items):
            bw = self.btn_start.geometry().center()
            self.fx.burst(QPointF(bw.x() - 30, self.height() - 80), 170)
            self.fx.burst(QPointF(self.width() * 0.25, self.height() - 60), 90)
            self.fx.show_toast(tr("all_done", size=human_size(saved)), GREEN)
        elif any(it.status == FAILED for it in self.items):
            self.fx.show_toast(tr("failed"), RED)

    def _refresh_stats(self):
        done = [it for it in self.items if it.status in (DONE, SKIPPED)]
        self.stats.set_totals(len(done), len(self.items), sum(i.in_size for i in done),
                              sum(i.out_size if i.status == DONE else i.in_size for i in done))

    def open_folder(self):
        if self.last_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_dir))

    def closeEvent(self, e):
        if self.worker:
            self.worker.cancel()
            self.worker.wait(4000)
        self._save()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("VidShrink")
    app.setStyle("Fusion")
    app.setWindowIcon(make_icon())
    win = MainWindow()
    scr = app.primaryScreen().availableGeometry()
    win.move(scr.center().x() - win.width() // 2, scr.center().y() - win.height() // 2)
    win.show()
    args = [a for a in sys.argv[1:] if os.path.exists(a)]
    if args:
        win.add_files(args)
    sys.exit(app.exec())
