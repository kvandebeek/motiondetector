from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal
import threading

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from analyzer.capture import Region


ResizeMode = Literal["none", "move", "l", "r", "t", "b", "tl", "tr", "bl", "br"]


@dataclass
class UiRegion:
    x: int
    y: int
    width: int
    height: int


class SelectorWindow(QWidget):
    def __init__(
        self,
        *,
        initial: UiRegion,
        border_px: int,
        grid_line_px: int,
        on_close: Callable[[], None],
        on_region_change: Callable[[Region], None],
    ) -> None:
        super().__init__()

        self._on_close = on_close
        self._on_region_change = on_region_change

        self._border_px = int(border_px)
        self._grid_line_px = int(grid_line_px)

        self._drag_mode: ResizeMode = "none"
        self._drag_start_pos = QPoint(0, 0)
        self._start_geom = QRect()

        self.setWindowTitle("motiondetector grid")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self.setGeometry(initial.x, initial.y, initial.width, initial.height)
        self._emit_region()

    def _emit_region(self) -> None:
        g = self.geometry()
        self._on_region_change(Region(x=int(g.x()), y=int(g.y()), width=int(g.width()), height=int(g.height())))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._on_close()
        event.accept()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Border
        pen = QPen(Qt.GlobalColor.cyan)
        pen.setWidth(self._border_px)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.rect().adjusted(self._border_px, self._border_px, -self._border_px, -self._border_px))

        # Grid lines
        pen2 = QPen(Qt.GlobalColor.cyan)
        pen2.setWidth(self._grid_line_px)
        pen2.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen2)

        w = self.width()
        h = self.height()
        x1 = w // 3
        x2 = (2 * w) // 3
        y1 = h // 3
        y2 = (2 * h) // 3

        p.drawLine(x1, 0, x1, h)
        p.drawLine(x2, 0, x2, h)
        p.drawLine(0, y1, w, y1)
        p.drawLine(0, y2, w, y2)

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

        # dragging
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
                g.setWidth(min_w)
            if g.height() < min_h:
                g.setHeight(min_h)

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
) -> None:
    app = QApplication([])
    w = SelectorWindow(
        initial=initial,
        border_px=border_px,
        grid_line_px=grid_line_px,
        on_close=on_close,
        on_region_change=on_region_change,
    )
    w.show()

    def poll_quit() -> None:
        if quit_flag.is_set():
            w.close()
        else:
            app.thread().msleep(200)  # lightweight
            poll_quit()

    # Minimal polling without timers to keep dependencies small:
    threading.Thread(target=lambda: app.exec(), daemon=False).start()
