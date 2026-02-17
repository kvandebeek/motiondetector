# ui/selector_ui.py
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable, Literal

from PySide6.QtCore import Qt, QRect, QPoint, QTimer
from PySide6.QtGui import QPainter, QPen
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
        # On Windows with display scaling this is typically 1.25 / 1.5 / 2.0 etc.
        # MSS captures in physical pixels, so capture coordinates must be scaled.
        try:
            return float(self.devicePixelRatioF())
        except Exception:
            # Conservative default: assume no scaling if DPR can't be queried.
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

        # Ensure a valid capture area (MSS expects positive size).
        w = max(1, w)
        h = max(1, h)

        self._on_region_change(Region(x=x, y=y, width=w, height=h))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Ensure the owning controller can react to the selector UI being closed.
        self._on_close()
        event.accept()

    @staticmethod
    def _edges(size: int, parts: int) -> list[int]:
        # Split [0..size] into 'parts' segments using rounding so boundaries line up.
        # Returns a list of pixel offsets with length parts+1 (including 0 and size).
        out = [int(round(i * size / parts)) for i in range(parts + 1)]
        out[0] = 0
        out[parts] = int(size)
        return out

    def paintEvent(self, event) -> None:  # type: ignore[override]
        # Draw the inner border + dashed grid overlay.
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

        # Compute rounded edges so each tile spans the full width/height without gaps.
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

    def _hit_test(self, pos: QPoint) -> ResizeMode:
        # Determine which resize handle (or move) the cursor is over.
        # Uses a margin around the window edges/corners, not the inner rect.
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
        # Start a move/resize operation on left click.
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_mode = self._hit_test(event.position().toPoint())
        self._drag_start_pos = event.globalPosition().toPoint()
        self._start_geom = self.geometry()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        # While not dragging: update cursor shape.
        # While dragging: update window geometry and emit the new capture region.
        pos = event.position().toPoint()
        mode = self._hit_test(pos)

        if self._drag_mode == "none":
            self._set_cursor(mode)
            return

        delta = event.globalPosition().toPoint() - self._drag_start_pos
        g = QRect(self._start_geom)

        # Prevent collapsing to near-zero size during resize.
        min_w = 120
        min_h = 90

        if self._drag_mode == "move":
            g.moveTo(self._start_geom.topLeft() + delta)
        else:
            dx = delta.x()
            dy = delta.y()

            # Adjust edges based on drag handle.
            if "l" in self._drag_mode:
                g.setLeft(g.left() + dx)
            if "r" in self._drag_mode:
                g.setRight(g.right() + dx)
            if "t" in self._drag_mode:
                g.setTop(g.top() + dy)
            if "b" in self._drag_mode:
                g.setBottom(g.bottom() + dy)

            # Clamp width/height by adjusting the active edge back into bounds.
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
        self._emit_region()  # keep capture region in sync with the current window geometry
        self.update()  # repaint border/grid after geometry changes

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        # End current move/resize operation.
        _ = event
        self._drag_mode = "none"

    def _set_cursor(self, mode: ResizeMode) -> None:
        # Map hit-test result to an appropriate cursor shape.
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
) -> None:
    # Entrypoint to run the selector UI in a thread: creates its own QApplication loop.
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
    )
    w.show()

    # Poll for the quit flag because this UI is typically controlled from another thread.
    timer = QTimer()
    timer.setInterval(200)

    def on_tick() -> None:
        # Stop the UI loop when the owner sets the quit flag.
        if quit_flag.is_set():
            timer.stop()
            w.close()
            app.quit()

    timer.timeout.connect(on_tick)  # type: ignore[arg-type]
    timer.start()

    app.exec()
