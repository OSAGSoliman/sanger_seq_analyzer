"""Linked forward/reverse chromatogram viewers (design spec 3.18, FR CH-1..CH-12).

Coordinates: a trace sample ``x`` of the read is drawn at ``x * scale`` pixels,
where ``scale = px_per_base / mean peak spacing``. A read aligned to the minus
strand is shown reverse-complemented by default: the trace is mirrored
(``x' = P - 1 - x``), channels A<->T and C<->G swap and letters are complemented,
so both tracks read left to right in reference orientation. Everything is
precomputed in that "display" space once per read; painting only touches the
visible samples.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QContextMenuEvent, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPen, QPolygonF, \
    QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea, QCheckBox, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)

from ...abi import Chromatogram
from ...pipeline import SampleResult
from ...presentation import AlignmentData, ReadView, position_readout, read_view
from ...qc import QCSettings
from ..theme import current
from ..theme.fonts import mono_font, ui_font
from .badge import Badge, kind_for_status
from .common import button, icon_button, label
from .cutoff_meter import CutoffMeter

ZOOM_LEVELS = (4, 7, 10, 14, 20, 28)  # pixels per base; 14 = 100%
DEFAULT_ZOOM = 14
COMPLEMENT = {"A": "T", "C": "G", "G": "C", "T": "A"}
_COMP_TABLE = str.maketrans("ACGTRYKMSWBDHVN", "TGCAYRMKSWVHDBN")
MINIMAP_H, MINIMAP_GAP, LETTER_H, RULER_H, QUAL_H = 10, 6, 18, 12, 30
MARGIN = 10
PSEUDO_SPACING = 12  # trace samples per base when a file has no trace data


@dataclass
class _Read:
    """One read in display order (reverse-complemented when ``rc``)."""

    calls: str
    quals: np.ndarray
    peaks: np.ndarray  # display trace x of each display base
    traces: dict[str, np.ndarray] | None  # display channel -> samples
    n_points: int
    trim: tuple[int, int]  # display indices [start, end)
    ref: np.ndarray  # display base -> 0-based reference position, -1 none, -2 inserted
    spacing: float
    ymax: float
    rc: bool
    n: int

    def raw_to_disp(self, raw: int) -> int:
        return self.n - 1 - raw if self.rc else raw

    def disp_to_raw(self, i: int) -> int:
        return self.n - 1 - i if self.rc else i


def _build(chrom: Chromatogram, trim: tuple[int, int], ref_map: np.ndarray, rc: bool) -> _Read:
    n = len(chrom)
    has_traces = chrom.has_traces and len(chrom.peak_locations) == n and n > 0
    if has_traces:
        p = min(len(chrom.traces[b]) for b in "ACGT")
        peaks = chrom.peak_locations.astype(float)
        traces = {b: chrom.traces[b][:p].astype(np.float32) for b in "ACGT"}
    else:
        p = n * PSEUDO_SPACING + PSEUDO_SPACING
        peaks = np.arange(n, dtype=float) * PSEUDO_SPACING + PSEUDO_SPACING / 2
        traces = None
    spacing = float((peaks[-1] - peaks[0]) / (n - 1)) if n > 1 else float(PSEUDO_SPACING)
    calls, quals, ref = chrom.sequence, np.asarray(chrom.qualities), ref_map
    t0, t1 = trim
    if rc:
        calls = calls.translate(_COMP_TABLE)[::-1]
        quals = quals[::-1]
        peaks = (p - 1 - peaks)[::-1]
        ref = ref[::-1]
        if traces is not None:
            traces = {b: traces[COMPLEMENT[b]][::-1].copy() for b in "ACGT"}
        t0, t1 = n - t1, n - t0
    ymax = 1.0
    if traces is not None and t1 > t0:
        lo = int(max(0, peaks[t0] - spacing / 2))
        hi = int(min(p, peaks[t1 - 1] + spacing / 2 + 1))
        ymax = max(1.0, max(float(traces[b][lo:hi].max(initial=0)) for b in "ACGT"))
    return _Read(calls, quals, peaks, traces, p, (t0, t1), ref, spacing, ymax, rc, n)


class TraceView(QAbstractScrollArea):
    """The scrollable trace area of one track."""

    baseClicked = Signal(int)  # raw (untrimmed, original orientation) base index
    viewMoved = Signal()  # the user scrolled or zoomed
    zoomRequested = Signal(int, float)  # step (+1/-1), anchor x in the viewport

    def __init__(self, parent=None):
        super().__init__(parent)
        self.read: _Read | None = None
        self.px_per_base = DEFAULT_ZOOM
        self.min_quality = 20
        self.selection: tuple[float, float] | None = None  # display x range of the selected band
        self.stop_band: tuple[float, float] | None = None
        self.note = ""
        self.message = ""  # placeholder text when there is nothing to draw
        self._drag: tuple[float, int] | None = None
        self._moved = False
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self._programmatic = False

    # --- model ------------------------------------------------------------------------------------------

    def set_read(self, read: _Read | None, message: str = "") -> None:
        self.read = read
        self.message = message
        self.selection = self.stop_band = None
        self.note = ""
        self._relayout()
        self.viewport().update()

    @property
    def scale(self) -> float:
        return self.px_per_base / self.read.spacing if self.read else 1.0

    def content_width(self) -> float:
        return self.read.n_points * self.scale + 2 * MARGIN if self.read else 0

    def _relayout(self) -> None:
        sb = self.horizontalScrollBar()
        self._programmatic = True
        sb.setRange(0, max(0, int(self.content_width() - self.viewport().width())))
        sb.setPageStep(self.viewport().width())
        sb.setSingleStep(max(1, int(self.px_per_base * 3)))
        self._programmatic = False

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def sizeHint(self) -> QSize:
        return QSize(700, 166)

    def _scrolled(self, _v) -> None:
        self.viewport().update()
        if not self._programmatic:
            self.viewMoved.emit()

    def set_zoom(self, px_per_base: float, anchor: float | None = None) -> None:
        """Change pixels per base, keeping the sample under ``anchor`` (viewport x) in place."""
        if not self.read:
            self.px_per_base = px_per_base
            return
        anchor = self.viewport().width() / 2 if anchor is None else anchor
        sample = self.sample_at(anchor)
        self.px_per_base = px_per_base
        self._relayout()
        self.scroll_to_sample(sample, anchor)

    def x_of(self, sample: float) -> float:
        """Viewport x of a display trace sample."""
        return sample * self.scale + MARGIN - self.horizontalScrollBar().value()

    def sample_at(self, x: float) -> float:
        return (x + self.horizontalScrollBar().value() - MARGIN) / self.scale

    def scroll_to_sample(self, sample: float, at: float | None = None) -> None:
        at = self.viewport().width() / 2 if at is None else at
        self._programmatic = True
        self.horizontalScrollBar().setValue(int(round(sample * self.scale + MARGIN - at)))
        self._programmatic = False
        self.viewport().update()

    def visible_samples(self) -> tuple[float, float]:
        return self.sample_at(0), self.sample_at(self.viewport().width())

    def nearest_base(self, sample: float) -> int | None:
        """Display index of the base whose peak is nearest to ``sample``."""
        if not self.read or not self.read.n:
            return None
        peaks = self.read.peaks
        i = bisect_left(peaks, sample)
        if i <= 0:
            return 0
        if i >= len(peaks):
            return len(peaks) - 1
        return i if peaks[i] - sample < sample - peaks[i - 1] else i - 1

    # --- painting ---------------------------------------------------------------------------------------

    def _rows(self) -> tuple[float, float, float, float, float]:
        """y of letters, ruler, traces top, traces bottom, quality top."""
        h = self.viewport().height()
        letters = MINIMAP_H + MINIMAP_GAP
        ruler = letters + LETTER_H
        trace_top = ruler + RULER_H + 2
        qual_top = h - QUAL_H
        return letters, ruler, trace_top, qual_top - 4, qual_top

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self.viewport())
        w, h = self.viewport().width(), self.viewport().height()
        p.fillRect(0, 0, w, h, t.c("bg.surface"))
        if not self.read:
            p.setFont(ui_font(12))
            p.setPen(t.c("text.tertiary"))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, self.message)
            return
        r = self.read
        letters_y, ruler_y, trace_top, trace_bottom, qual_top = self._rows()
        s0, s1 = self.visible_samples()
        lo = max(0, bisect_left(r.peaks, s0 - r.spacing * 2))
        hi = min(r.n, bisect_left(r.peaks, s1 + r.spacing * 2))
        p.fillRect(QRectF(0, qual_top, w, QUAL_H), t.c("bg.sunken"))
        self._paint_bands(p, trace_top, h)
        if r.traces is not None:
            self._paint_traces(p, trace_top, trace_bottom, s0, s1)
        self._paint_bases(p, lo, hi, letters_y, ruler_y, qual_top)
        self._paint_trim(p, h, qual_top)
        self._paint_minimap(p, w)
        if self.note:
            p.setFont(ui_font(11))
            p.setPen(t.c("text.tertiary"))
            p.drawText(QRectF(8, trace_top, w - 16, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                       self.note)

    def _paint_bands(self, p: QPainter, top: float, h: float) -> None:
        t = current()
        for band, fill, edge in ((self.stop_band, "chrom.stop.fill", "chrom.stop.edge"),
                                 (self.selection, "chrom.sel.fill", "chrom.sel.edge")):
            if band is None:
                continue
            x0, x1 = self.x_of(band[0]), self.x_of(band[1])
            p.fillRect(QRectF(x0, MINIMAP_H + MINIMAP_GAP, x1 - x0, h), t.c(fill))
            p.fillRect(QRectF(x0, MINIMAP_H + MINIMAP_GAP, 1, h), t.c(edge))
            p.fillRect(QRectF(x1 - 1, MINIMAP_H + MINIMAP_GAP, 1, h), t.c(edge))

    def _paint_traces(self, p: QPainter, top: float, bottom: float, s0: float, s1: float) -> None:
        t = current()
        r = self.read
        a = max(0, int(s0) - 4)
        b = min(r.n_points, int(s1) + 6)
        if b - a < 2:
            return
        height = bottom - top - 6
        offset = self.horizontalScrollBar().value() - MARGIN
        per_px = 1 / self.scale
        envelope = per_px > 1.0  # several samples per pixel: a 1 px min/max envelope looks the same, much faster
        p.setRenderHint(QPainter.RenderHint.Antialiasing, not envelope)
        pen = QPen()
        pen.setCosmetic(True)
        pen.setWidthF(1.0 if envelope else 1.25)
        for base in "GATC":  # darker G behind
            y = r.traces[base][a:b]
            ys = bottom - np.minimum(y / r.ymax, 1.02) * height
            if envelope:
                cols = ((np.arange(a, b) * self.scale - offset)).astype(int)
                starts = np.flatnonzero(np.r_[True, cols[1:] != cols[:-1]])
                mins = np.minimum.reduceat(ys, starts)
                maxs = np.maximum.reduceat(ys, starts)
                xs = cols[starts].astype(float)
                pts = [QPointF(x, v) for x, lo_, hi_ in zip(xs, mins, maxs) for v in (lo_, hi_)]
            else:
                xs = np.arange(a, b) * self.scale - offset
                pts = [QPointF(x, v) for x, v in zip(xs.tolist(), ys.tolist())]
            pen.setColor(t.base(base))
            p.setPen(pen)
            p.drawPolyline(QPolygonF(pts))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _paint_bases(self, p: QPainter, lo: int, hi: int, letters_y: float, ruler_y: float, qual_top: float) -> None:
        t = current()
        r = self.read
        show_text = self.px_per_base >= 7
        bar_w = max(1.0, 0.57 * self.px_per_base)
        letter_font, ruler_font = mono_font(12, 700), ui_font(9, tabular=True)
        good, bad = t.c("chrom.qual.good"), t.c("chrom.qual.bad")
        aligned = bool((r.ref >= 0).any())
        for i in range(lo, hi):
            x = self.x_of(r.peaks[i])
            q = int(r.quals[i])
            bh = min(q, 60) / 60 * (QUAL_H - 2)
            p.fillRect(QRectF(x - bar_w / 2, qual_top + QUAL_H - bh, bar_w, bh), good if q >= self.min_quality else bad)
            if not show_text:
                continue
            c = r.calls[i]
            p.setFont(letter_font)
            p.setPen(t.base(c))
            p.drawText(QRectF(x - 8, letters_y, 16, LETTER_H), Qt.AlignmentFlag.AlignCenter, c)
            if aligned:
                number = int(r.ref[i]) + 1 if r.ref[i] >= 0 and (r.ref[i] + 1) % 10 == 0 else None
            else:
                raw = r.disp_to_raw(i) + 1
                number = raw if raw % 10 == 0 else None
            if number:
                p.setFont(ruler_font)
                p.setPen(t.c("text.tertiary"))
                p.drawText(QRectF(x - 20, ruler_y, 40, RULER_H), Qt.AlignmentFlag.AlignCenter, str(number))
        y = qual_top + QUAL_H - min(self.min_quality, 60) / 60 * (QUAL_H - 2)
        pen = QPen(t.c("chrom.qual.line"), 1, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        p.drawLine(QPointF(0, y), QPointF(self.viewport().width(), y))

    def _paint_trim(self, p: QPainter, h: float, qual_top: float) -> None:
        t = current()
        r = self.read
        top = MINIMAP_H + MINIMAP_GAP
        edges = []
        if r.trim[0] > 0:
            edge = (r.peaks[r.trim[0] - 1] + r.peaks[r.trim[0]]) / 2 if r.trim[0] < r.n else r.n_points
            x = self.x_of(edge)
            p.fillRect(QRectF(self.x_of(0), top, x - self.x_of(0), h - top), t.c("chrom.trim.fill"))
            edges.append((x, "left"))
        if r.trim[1] < r.n:
            x = self.x_of((r.peaks[r.trim[1] - 1] + r.peaks[r.trim[1]]) / 2) if r.trim[1] > 0 else self.x_of(0)
            p.fillRect(QRectF(x, top, self.x_of(r.n_points) - x, h - top), t.c("chrom.trim.fill"))
            edges.append((x, "right"))
        pen = QPen(t.c("chrom.trim.edge"), 1, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3, 3])
        for x, side in edges:
            if not -1 <= x <= self.viewport().width() + 1:
                continue
            p.setPen(pen)
            p.drawLine(QPointF(x, top), QPointF(x, h))
            raw = r.disp_to_raw(r.trim[1] - 1 if side == "right" else r.trim[0]) + 1
            text = f"Trimmed · read {'ends' if side == 'right' else 'starts'} at base {raw}"
            p.setFont(ui_font(10))
            p.setPen(t.c("text.tertiary"))
            box = QRectF(x + 6, qual_top - 16, 220, 14) if side == "right" else QRectF(x - 226, qual_top - 16, 220, 14)
            align = Qt.AlignmentFlag.AlignLeft if side == "right" else Qt.AlignmentFlag.AlignRight
            p.drawText(box, align | Qt.AlignmentFlag.AlignVCenter, text)

    def _minimap_scale(self, w: float) -> float:
        return (w - 2) / max(1.0, self.read.n_points)

    def _paint_minimap(self, p: QPainter, w: float) -> None:
        t = current()
        r = self.read
        k = self._minimap_scale(w)
        rect = QRectF(1, 0, w - 2, MINIMAP_H)
        p.fillRect(rect, t.c("chrom.mini.ground"))
        hatch = QBrush(t.c("chrom.mini.hatch"), Qt.BrushStyle.BDiagPattern)
        if r.trim[0] > 0 and r.n:
            p.fillRect(QRectF(1, 0, r.peaks[min(r.trim[0], r.n - 1)] * k, MINIMAP_H), hatch)
        if r.trim[1] < r.n:
            x = r.peaks[max(0, r.trim[1] - 1)] * k
            p.fillRect(QRectF(1 + x, 0, w - 2 - x, MINIMAP_H), hatch)
        on_ref = np.flatnonzero(r.ref >= 0)
        if on_ref.size:
            x0, x1 = r.peaks[on_ref[0]] * k, r.peaks[on_ref[-1]] * k
            p.fillRect(QRectF(1 + x0, 2, max(2.0, x1 - x0), MINIMAP_H - 4), t.c("chrom.mini.aligned"))
        s0, s1 = self.visible_samples()
        view = QRectF(1 + max(0.0, s0) * k, 0.75, max(4.0, (min(s1, r.n_points) - max(0.0, s0)) * k), MINIMAP_H - 1.5)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(t.c("chrom.mini.viewFill"))
        p.setPen(QPen(t.c("chrom.mini.view"), 1.5))
        p.drawRoundedRect(view, 2, 2)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    # --- interaction ------------------------------------------------------------------------------------

    def viewportEvent(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent) and self.read:
            if e.pos().y() > MINIMAP_H + MINIMAP_GAP:
                i = self.nearest_base(self.sample_at(e.pos().x()))
                if i is not None:
                    QToolTip.showText(e.globalPos(), self.parent_track().base_tooltip(i), self.viewport())
                    return True
            QToolTip.hideText()
            return True
        return super().viewportEvent(e)

    def parent_track(self) -> ChromatogramTrack:
        w = self.parentWidget()
        while w is not None and not isinstance(w, ChromatogramTrack):
            w = w.parentWidget()
        return w

    def wheelEvent(self, e: QWheelEvent) -> None:
        if not self.read:
            return
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = 1 if e.angleDelta().y() > 0 else -1
            self.zoomRequested.emit(steps, e.position().x())
            return
        delta = e.pixelDelta().x() or e.pixelDelta().y()
        if not delta:
            d = e.angleDelta()
            delta = (d.x() or d.y()) / 120 * self.px_per_base * 6
        sb = self.horizontalScrollBar()
        sb.setValue(int(sb.value() - delta))

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if not self.read or e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        self.setFocus()
        if e.position().y() <= MINIMAP_H + 2:
            self._minimap_jump(e.position().x())
            self._drag = (-1.0, 0)
            return
        self._drag = (e.position().x(), self.horizontalScrollBar().value())
        self._moved = False

    def _minimap_jump(self, x: float) -> None:
        sample = (x - 1) / self._minimap_scale(self.viewport().width())
        self.scroll_to_sample(sample)
        self.viewMoved.emit()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            return
        if self._drag[0] < 0:
            self._minimap_jump(e.position().x())
            return
        dx = e.position().x() - self._drag[0]
        if abs(dx) > 3:
            self._moved = True
            self.horizontalScrollBar().setValue(int(self._drag[1] - dx))

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        drag, self._drag = self._drag, None
        if drag is None or drag[0] < 0 or self._moved or not self.read:
            return
        i = self.nearest_base(self.sample_at(e.position().x()))
        if i is not None:
            self.baseClicked.emit(self.read.disp_to_raw(i))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        sb = self.horizontalScrollBar()
        if e.key() == Qt.Key.Key_Right:
            sb.setValue(sb.value() + sb.singleStep())
        elif e.key() == Qt.Key.Key_Left:
            sb.setValue(sb.value() - sb.singleStep())
        elif e.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoomRequested.emit(1, self.viewport().width() / 2)
        elif e.key() == Qt.Key.Key_Minus:
            self.zoomRequested.emit(-1, self.viewport().width() / 2)
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e: QContextMenuEvent) -> None:
        track = self.parent_track()
        if track is None or track.strand != "-":
            return
        menu = QMenu(self)
        act = menu.addAction("Show as sequenced")
        act.setCheckable(True)
        act.setChecked(not track.rc_enabled)
        act.toggled.connect(lambda on: track.set_reverse_complement(not on))
        menu.exec(e.globalPos())


class ReadQcColumn(QWidget):
    """The 232 px column at the left of a track (design spec 3.18)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(232)
        self.setProperty("surface", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = label("", "panelTitle")
        self.badge = Badge("pass")
        head.addWidget(self.title)
        head.addWidget(self.badge)
        head.addStretch(1)
        lay.addLayout(head)
        self.file = label("", "monoHint")
        self.file.setWordWrap(True)
        lay.addWidget(self.file)
        self.orientation = label("", "hint")
        lay.addWidget(self.orientation)
        self.meter = CutoffMeter()
        lay.addSpacing(2)
        lay.addWidget(self.meter)
        self.rows = QLabel()
        self.rows.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.rows)
        self.problems = label("", "error", wrap=True)
        lay.addWidget(self.problems)
        lay.addStretch(1)

    def set_view(self, v: ReadView) -> None:
        t = current()
        self.title.setText(v.label)
        self.badge.set(kind_for_status(v.status), v.status)
        self.file.setText(v.file_name)
        self.file.setVisible(bool(v.file_name))
        self.orientation.setText(v.orientation[:1].upper() + v.orientation[1:])
        self.orientation.setVisible(bool(v.orientation))
        ok = v.state in ("ok", "not_assembled")
        self.meter.setVisible(ok)
        if ok:
            self.meter.set(v.good_fraction, v.cutoff, v.min_quality, v.meter_text, v.meter_failed)
        rows = "".join(
            f"<tr><td style='color:{t['text.tertiary']}; padding-right:8px'>{k}</td>"
            f"<td align='right' style='color:{t['text.primary']}'>{val}</td></tr>" for k, val in v.rows)
        self.rows.setText(f"<table width='100%' cellspacing='0' cellpadding='1' style='font-size:12px'>{rows}</table>")
        self.rows.setVisible(bool(v.rows))
        problems = list(v.problems) + ([v.notice] if v.notice else [])
        self.problems.setText("\n".join(p[:1].upper() + p[1:] for p in problems))
        self.problems.setVisible(bool(problems))


class ChromatogramTrack(QWidget):
    """Read-QC column plus trace view for one direction."""

    columnClicked = Signal(int)

    def __init__(self, direction: str, parent=None):
        super().__init__(parent)
        self.direction = direction
        self.qc = ReadQcColumn()
        self.view = TraceView()
        self.view.baseClicked.connect(self._base_clicked)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.qc)
        lay.addWidget(_Line(horizontal=False))
        lay.addWidget(self.view, 1)
        self.chrom: Chromatogram | None = None
        self.strand: str | None = None
        self.rc_enabled = True
        self.raw_to_col: dict[int, int] = {}
        self.col_to_raw: list[int | None] = []
        self.data: AlignmentData | None = None
        self._ref_map = np.zeros(0, dtype=int)
        self._trim = (0, 0)
        self.selected_raw: int | None = None
        self.selected_column: int | None = None
        self.missing = False

    def set_sample(self, sr: SampleResult, data: AlignmentData | None, settings: QCSettings) -> None:
        v = read_view(sr, self.direction, settings)
        self.qc.set_view(v)
        self.view.min_quality = settings.min_quality
        self.data = data
        self.chrom = sr.chrom(self.direction) if v.state in ("ok", "not_assembled") else None
        call = sr.consensus
        a = next((x for x in call.aligned if x.direction == self.direction), None)
        self.strand = a.strand if a else None
        self.raw_to_col = data.raw_to_col.get(self.direction, {}) if data else {}
        self.col_to_raw = data.col_to_raw.get(self.direction, []) if data else []
        read = call.reads.get(self.direction)
        self.missing = v.state == "missing"
        self.selected_raw = self.selected_column = None
        if self.chrom is None:
            self._ref_map = np.zeros(0, dtype=int)
            self.view.set_read(None, {"missing": f"{v.label} read missing",
                                      "unreadable": "File could not be read"}.get(v.state, ""))
        else:
            self._ref_map = a.ref_map(len(self.chrom)) if a else np.full(len(self.chrom), -1)
            self._trim = (read.trim_start, read.trim_end) if read else (0, len(self.chrom))
            self._rebuild()
        height = 64 if self.chrom is None else max(160, self.qc.sizeHint().height())
        self.setMinimumHeight(height)
        self.setMaximumHeight(64 if self.chrom is None else 16777215)

    def _rebuild(self) -> None:
        rc = self.strand == "-" and self.rc_enabled
        self.view.set_read(_build(self.chrom, self._trim, self._ref_map, rc))

    def set_reverse_complement(self, on: bool) -> None:
        self.rc_enabled = on
        if self.chrom is not None:
            center = self.center_column()
            self._rebuild()
            self.view.set_zoom(self.view.px_per_base)
            if self.selected_column is not None:
                self.select_column(self.selected_column, center=True)
            elif center is not None:
                self.center_on_column(center)

    # --- selection and linking --------------------------------------------------------------------------

    def _disp_sample_of_raw(self, raw: int) -> float:
        r = self.view.read
        return float(r.peaks[r.raw_to_disp(raw)])

    def _target(self, column: int) -> tuple[float, float] | None:
        """Display x range of the band for a column, or None when this read does not cover it."""
        if self.data is None or not 0 <= column < len(self.data.columns) or self.view.read is None:
            return None
        cell = self.data.columns[column].reads.get(self.direction)
        if cell is None:
            return None
        half = self.view.read.spacing / 2
        if cell.raw is not None:
            x = self._disp_sample_of_raw(cell.raw)
            return x - half, x + half
        a, b = cell.between
        xs = [self._disp_sample_of_raw(r) for r in (a, b) if r is not None]
        if not xs:
            return None
        mid = sum(xs) / len(xs)
        return mid - half / 2, mid + half / 2

    def select_column(self, column: int | None, center: bool = True) -> bool:
        """Highlight a column; returns False (and leaves the view) when the read does not cover it."""
        self.selected_column = column
        self.view.stop_band = None
        if column is None or self.view.read is None:
            self.view.selection = None
            self.selected_raw = None
            self.view.note = ""
            self.view.viewport().update()
            return False
        band = self._target(column)
        self.selected_raw = self.col_to_raw[column] if column < len(self.col_to_raw) else None
        if band is None:
            self.view.selection = None
            self.view.note = "Position not covered by this read"
            self.view.viewport().update()
            return False
        self.view.note = ""
        self.view.selection = band
        stop = next((s for s in (self.data.stops if self.data else []) if column in s), None)
        if stop:
            bands = [b for b in (self._target(c) for c in stop) if b]
            if bands:
                self.view.stop_band = (min(b[0] for b in bands), max(b[1] for b in bands))
        if center:
            self.view.scroll_to_sample((band[0] + band[1]) / 2)
        self.view.viewport().update()
        return True

    def center_column(self) -> int | None:
        """Alignment column of the aligned base nearest to the viewport centre."""
        r = self.view.read
        if r is None or not self.raw_to_col:
            return None
        i = self.view.nearest_base(self.view.sample_at(self.view.viewport().width() / 2))
        if i is None:
            return None
        for step in range(0, r.n):
            for j in (i - step, i + step):
                if 0 <= j < r.n and r.disp_to_raw(j) in self.raw_to_col:
                    return self.raw_to_col[r.disp_to_raw(j)]
        return None

    def center_on_column(self, column: int) -> None:
        band = self._target(column)
        if band is not None:
            self.view.scroll_to_sample((band[0] + band[1]) / 2)

    def _base_clicked(self, raw: int) -> None:
        col = self.raw_to_col.get(raw)
        if col is not None:
            self.columnClicked.emit(col)

    def base_tooltip(self, i: int) -> str:
        r = self.view.read
        raw = r.disp_to_raw(i)
        parts = [f"Read base {raw + 1}"]
        ref = int(r.ref[i])
        parts.append(f"ref {ref + 1}" if ref >= 0 else ("insertion" if ref == -2 else "not aligned"))
        parts.append(f"called {r.calls[i]}")
        parts.append(f"Q{int(r.quals[i])}")
        if self.data is not None and raw in self.raw_to_col:
            cell = self.data.columns[self.raw_to_col[raw]].reads.get(self.direction)
            if cell is not None and cell.uncalled and cell.ratio >= 0.05:
                parts.append(f"secondary {cell.uncalled} {cell.ratio:.0%}")
        if not self._trim[0] <= raw < self._trim[1]:
            parts.append("trimmed")
        return " · ".join(parts)

    def first_visible_call(self) -> str:
        r = self.view.read
        i = self.view.nearest_base(max(0.0, self.view.sample_at(0)))
        return r.calls[i] if r is not None and i is not None else ""


class ChromatogramPanel(QWidget):
    """Toolbar plus forward and reverse tracks, linked by reference position (CH-5, CH-7)."""

    columnClicked = Signal(int)
    zoomChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("surface", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.data: AlignmentData | None = None
        self.sr: SampleResult | None = None
        self.zoom = DEFAULT_ZOOM
        self._linking = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget()
        bar.setFixedHeight(38)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 8, 0)
        bl.setSpacing(8)
        bl.addWidget(label("Chromatograms", "panelTitle"))
        self.chip = QLabel("")
        self.chip.setProperty("role", "chip")
        bl.addWidget(self.chip)
        bl.addStretch(1)
        self.link = QCheckBox("Link F and R")
        self.link.setChecked(True)
        bl.addWidget(self.link)
        bl.addSpacing(8)
        self.zoom_out = icon_button("minus", "Zoom out (Ctrl+-)")
        self.zoom_label = label("100%", "secondary")
        self.zoom_label.setFixedWidth(42)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in = icon_button("plus", "Zoom in (Ctrl++)")
        bl.addWidget(self.zoom_out)
        bl.addWidget(self.zoom_label)
        bl.addWidget(self.zoom_in)
        self.fit = button("Fit read", size="small", icon_name="fit")
        bl.addWidget(self.fit)
        lay.addWidget(bar)
        self.tracks = {d: ChromatogramTrack(d) for d in ("F", "R")}
        for d in ("F", "R"):
            lay.addWidget(_Line(horizontal=True))
            lay.addWidget(self.tracks[d], 1)
            track = self.tracks[d]
            track.columnClicked.connect(self.columnClicked)
            track.view.viewMoved.connect(lambda d=d: self._view_moved(d))
            track.view.zoomRequested.connect(self._zoom_step_at)
        self.zoom_in.clicked.connect(lambda: self.zoom_step(1))
        self.zoom_out.clicked.connect(lambda: self.zoom_step(-1))
        self.fit.clicked.connect(self.fit_read)
        self._set_chip("")

    def _set_chip(self, text: str) -> None:
        t = current()
        self.chip.setText(text)
        self.chip.setVisible(bool(text))
        self.chip.setStyleSheet(f"background:{t['accent.subtle']}; color:{t['accent.onSubtle']}; border-radius:4px;"
                                "padding:2px 8px; font-size:12px;")

    def set_sample(self, sr: SampleResult, data: AlignmentData | None, settings: QCSettings) -> None:
        self.sr, self.data = sr, data
        for track in self.tracks.values():
            track.set_sample(sr, data, settings)
            track.view.set_zoom(self.zoom)
            track.view.scroll_to_sample(0, 0)
        self._set_chip("")
        self._center_first_aligned()

    def _center_first_aligned(self) -> None:
        for track in self.tracks.values():
            r = track.view.read
            if r is None:
                continue
            on_ref = np.flatnonzero(r.ref >= 0)
            first = on_ref[0] if on_ref.size else r.trim[0]
            if first < r.n:
                track.view.scroll_to_sample(float(r.peaks[first]) - r.spacing * 2, 0)

    def select_column(self, column: int | None) -> None:
        covered = {d: t.select_column(column) for d, t in self.tracks.items()}
        if column is None or self.data is None:
            self._set_chip("")
            return
        text = position_readout(self.data.columns[column], self.sr.consensus if self.sr else None)
        missing = [("forward" if d == "F" else "reverse") for d, ok in covered.items()
                   if not ok and self.tracks[d].view.read is not None]
        if missing:
            text += f" · not covered by the {' and '.join(missing)} read"
        self._set_chip(text)

    def _view_moved(self, direction: str) -> None:
        if self._linking or not self.link.isChecked():
            return
        column = self.tracks[direction].center_column()
        if column is None:
            return
        self._linking = True
        for d, track in self.tracks.items():
            if d != direction:
                track.center_on_column(column)
        self._linking = False

    def _zoom_step_at(self, step: int, anchor: float) -> None:
        self.zoom_step(step, anchor)

    def zoom_step(self, step: int, anchor: float | None = None) -> None:
        levels = list(ZOOM_LEVELS)
        if step > 0:
            nxt = next((z for z in levels if z > self.zoom + 0.01), levels[-1])
        else:
            nxt = next((z for z in reversed(levels) if z < self.zoom - 0.01), levels[0])
        self.set_zoom(nxt, anchor)

    def reset_zoom(self) -> None:
        self.set_zoom(DEFAULT_ZOOM)

    def set_zoom(self, px_per_base: float, anchor: float | None = None) -> None:
        self.zoom = px_per_base
        for track in self.tracks.values():
            track.view.set_zoom(px_per_base, anchor)
        if self.link.isChecked():
            self._view_moved("F" if self.tracks["F"].view.read is not None else "R")
        self.zoom_label.setText(f"{px_per_base / DEFAULT_ZOOM:.0%}")
        self.zoomChanged.emit(int(px_per_base))

    def fit_read(self) -> None:
        reads = [t.view for t in self.tracks.values() if t.view.read is not None]
        if not reads:
            return
        v = max(reads, key=lambda v: v.read.n)
        px = max(0.5, (v.viewport().width() - 2 * MARGIN) / max(1, v.read.n_points) * v.read.spacing)
        self.set_zoom(px)
        for view in reads:
            view.scroll_to_sample(0, 0)


class _Line(QWidget):
    """1 px divider in the border colour."""

    def __init__(self, horizontal: bool):
        super().__init__()
        if horizontal:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(1)

    def paintEvent(self, _e) -> None:
        QPainter(self).fillRect(self.rect(), current().c("border.default"))

