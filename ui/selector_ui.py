# ui/selector_ui.py
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable, Literal

from PySide6.QtCore import Qt, QRect, QPoint, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from analyzer.capture import Region

# Mouse interaction modes: either moving the window or resizing via an edge/corner.
ResizeMode = Literal["none", "move", "l", "r", "t", "b", "tl", "tr", "bl", "br"]


@dataclass
class UiRegion:
    # Initial window geometry in Qt *logical* pixels.
    x: int
    y: int
    width: int
    height: int


def _round_int(x: float) -> int:
    # Consistent rounding when converting float pixel values to int pixel coordinates.
    return int(round(x))


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


class SelectorWindow(QWidget):
    def __init__(
        self,
        *,
        initial: UiRegion,
        border_px: int,
        grid_line_px: int,
        on_close: Callable[[], None],
        on_region_change: Callable[[Region], None],
        grid_rows: int = 3,
        grid_cols: int = 3,
        emit_inset_px: int = 10,
        tile_label_text_color: str = "#FFFFFF",
        show_tile_numbers: bool = True,
    ) -> None:
        super().__init__()

        # Callbacks for lifecycle + for emitting the capture region to the monitor.
        self._on_close = on_close
        self._on_region_change = on_region_change

        # Visual border thickness and grid line thickness (both in logical px).
        self._border_px = int(border_px)
        self._grid_line_px = int(grid_line_px)

        # Logical grid dimensions (rows/cols) used for drawing the dashed overlay.
        self._grid_rows = int(grid_rows)
        self._grid_cols = int(grid_cols)
        if self._grid_rows <= 0 or self._grid_cols <= 0:
            raise ValueError("grid_rows and grid_cols must be > 0")

        # Extra inset for emitted capture region so the border and some padding are excluded.
        # This helps avoid the overlay itself being captured/affecting motion detection.
        self._emit_inset_px = int(emit_inset_px)

        # Tile labels toggle.
        self._show_tile_numbers = bool(show_tile_numbers)

        # Tile label colors.
        self._tile_label_bg = QColor(0, 0, 0, 140)
        self._tile_label_fg = QColor(tile_label_text_color)
        if not self._tile_label_fg.isValid():
            self._tile_label_fg = QColor("#FFFFFF")
        self._tile_label_fg.setAlpha(230)

        # Drag state: what operation is active, and the start state for delta calculations.
        self._drag_mode: ResizeMode = "none"
        self._drag_start_pos = QPoint(0, 0)  # global mouse position at press time
        self._start_geom = QRect()  # widget geometry at press time

        # Frameless transparent always-on-top tool window.
        self.setWindowTitle("motiondetector grid")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)  # needed to update cursor shape without dragging

        # Apply the initial geometry and immediately emit the corresponding capture region.
        self.setGeometry(initial.x, initial.y, initial.width, initial.height)
        self._emit_region()

    def set_show_tile_numbers(self, enabled: bool) -> None:
        v = bool(enabled)
        if v == self._show_tile_numbers:
            return
        self._show_tile_numbers = v
        self.update()

    def _inner_rect(self) -> QRect:
        # Compute the drawable/captured area inside the border and extra inset.
        # Returned rect is in widget-local logical pixels.
        inset = max(0, self._border_px + self._emit_inset_px)
        r = self.rect().adjusted(inset, inset, -inset, -inset)
        if r.width() < 1 or r.height() < 1:
            # Fallback when the window is too small: never return an empty rect.
            return QRect(0, 0, max(1, self.width()), max(1, self.height()))
        return r

    def _dpr(self) -> float:
        # Device pixel ratio (DPR) is used to convert logical (Qt) pixels to physical pixels.
        try:
            return float(self.devicePixelRatioF())
        except Exception:
            return 1.0

    def _emit_region(self) -> None:
        # Emit the current capture region in *physical* pixels to downstream capture code.
        inner = self._inner_rect()

        # Top-left in global coordinates, still in Qt logical pixel units.
        tl_logical = self.mapToGlobal(inner.topLeft())
        dpr = self._dpr()

        # Convert logical coords/sizes to physical pixels for screen capture.
        x = _round_int(float(tl_logical.x()) * dpr)
        y = _round_int(float(tl_logical.y()) * dpr)
        w = _round_int(float(inner.width()) * dpr)
        h = _round_int(float(inner.height()) * dpr)

        w = max(1, w)
        h = max(1, h)

        self._on_region_change(Region(x=x, y=y, width=w, height=h))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._on_close()
        event.accept()

    @staticmethod
    def _edges(size: int, parts: int) -> list[int]:
        out = [int(round(i * size / parts)) for i in range(parts + 1)]
        out[0] = 0
        out[parts] = int(size)
        return out

    def _tile_font(self, *, tile_h: int) -> QFont:
        px = _clamp_int(int(round(tile_h * 0.24)), 10, 32)
        f = QFont()
        f.setPixelSize(px)
        f.setBold(True)
        return f

    def _draw_centered_tile_label(self, p: QPainter, *, tile: QRect, label: str) -> None:
        fm = QFontMetrics(p.font())
        tw = fm.horizontalAdvance(label)
        th = fm.height()

        pad = _clamp_int(int(round(min(tile.width(), tile.height()) * 0.06)), 4, 10)
        bw = min(tile.width(), tw + 2 * pad)
        bh = min(tile.height(), th + 2 * pad)

        bx = tile.left() + max(0, (tile.width() - bw) // 2)
        by = tile.top() + max(0, (tile.height() - bh) // 2)
        bg = QRect(bx, by, bw, bh)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._tile_label_bg)
        radius = _clamp_int(int(round(min(bg.width(), bg.height()) * 0.22)), 4, 12)
        p.drawRoundedRect(bg, radius, radius)

        p.setPen(self._tile_label_fg)
        p.drawText(bg, Qt.AlignmentFlag.AlignCenter, label)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        inner = self._inner_rect()

        # Solid border.
        pen = QPen(Qt.GlobalColor.cyan)
        pen.setWidth(self._border_px)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(inner)

        # Dashed grid lines.
        pen2 = QPen(Qt.GlobalColor.cyan)
        pen2.setWidth(self._grid_line_px)
        pen2.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen2)

        w = inner.width()
        h = inner.height()

        x_edges = self._edges(w, self._grid_cols)
        y_edges = self._edges(h, self._grid_rows)

        left = inner.left()
        top = inner.top()
        right = inner.right()
        bottom = inner.bottom()

        # Vertical grid lines.
        for i in range(1, self._grid_cols):
            x = left + x_edges[i]
            p.drawLine(x, top, x, bottom)

        # Horizontal grid lines.
        for i in range(1, self._grid_rows):
            y = top + y_edges[i]
            p.drawLine(left, y, right, y)

        if not self._show_tile_numbers:
            return

        # Centered tile numbers (1..N), left-to-right, top-to-bottom.
        p.setFont(self._tile_font(tile_h=max(1, h // self._grid_rows)))

        for row in range(self._grid_rows):
            y0 = top + y_edges[row]
            y1 = top + y_edges[row + 1]
            for col in range(self._grid_cols):
                x0 = left + x_edges[col]
                x1 = left + x_edges[col + 1]

                tile = QRect(
                    x0,
                    y0,
                    max(1, x1 - x0),
                    max(1, y1 - y0),
                ).adjusted(0, 0, -1, -1)

                idx = row * self._grid_cols + col + 1
                self._draw_centered_tile_label(p, tile=tile, label=str(idx))

    def _hit_test(self, pos: QPoint) -> ResizeMode:
        margin = 12
        x = pos.x()
        y = pos.y()
        w = self.width()
        h = self.height()

        left = x <= margin
        right = x >= w - margin
        top = y <= margin
        bottom = y >= h - margin

        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        if left:
            return "l"
        if right:
            return "r"
        if top:
            return "t"
        if bottom:
            return "b"
        return "move"

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_mode = self._hit_test(event.position().toPoint())
        self._drag_start_pos = event.globalPosition().toPoint()
        self._start_geom = self.geometry()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        pos = event.position().toPoint()
        mode = self._hit_test(pos)

        if self._drag_mode == "none":
            self._set_cursor(mode)
            return

        delta = event.globalPosition().toPoint() - self._drag_start_pos
        g = QRect(self._start_geom)

        min_w = 120
        min_h = 90

        if self._drag_mode == "move":
            g.moveTo(self._start_geom.topLeft() + delta)
        else:
            dx = delta.x()
            dy = delta.y()

            if "l" in self._drag_mode:
                g.setLeft(g.left() + dx)
            if "r" in self._drag_mode:
                g.setRight(g.right() + dx)
            if "t" in self._drag_mode:
                g.setTop(g.top() + dy)
            if "b" in self._drag_mode:
                g.setBottom(g.bottom() + dy)

            if g.width() < min_w:
                if "l" in self._drag_mode:
                    g.setLeft(g.right() - min_w)
                else:
                    g.setRight(g.left() + min_w)

            if g.height() < min_h:
                if "t" in self._drag_mode:
                    g.setTop(g.bottom() - min_h)
                else:
                    g.setBottom(g.top() + min_h)

        self.setGeometry(g)
        self._emit_region()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        self._drag_mode = "none"

    def _set_cursor(self, mode: ResizeMode) -> None:
        if mode in ("l", "r"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif mode in ("t", "b"):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif mode in ("tl", "br"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif mode in ("tr", "bl"):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)


def run_selector_ui(
    *,
    initial: UiRegion,
    border_px: int,
    grid_line_px: int,
    on_close: Callable[[], None],
    on_region_change: Callable[[Region], None],
    quit_flag: threading.Event,
    grid_rows: int = 3,
    grid_cols: int = 3,
    emit_inset_px: int = 10,
    tile_label_text_color: str = "#FFFFFF",
    show_tile_numbers: bool = True,
) -> None:
    app = QApplication([])
    w = SelectorWindow(
        initial=initial,
        border_px=border_px,
        grid_line_px=grid_line_px,
        on_close=on_close,
        on_region_change=on_region_change,
        grid_rows=grid_rows,
        grid_cols=grid_cols,
        emit_inset_px=emit_inset_px,
        tile_label_text_color=tile_label_text_color,
        show_tile_numbers=show_tile_numbers,
    )
    w.show()

    timer = QTimer()
    timer.setInterval(200)

    def on_tick() -> None:
        if quit_flag.is_set():
            timer.stop()
            w.close()
            app.quit()

    timer.timeout.connect(on_tick)  # type: ignore[arg-type]
    timer.start()

    app.exec()
