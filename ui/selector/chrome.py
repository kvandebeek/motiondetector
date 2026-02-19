# File commentary: ui/selector/chrome.py - This file holds logic used by the motion detector project.
# ui/selector_chrome.py
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ui.selector.models import clamp_int


@dataclass(frozen=True)
class ChromeConfig:
    """
    Geometry and sizing config for the selector overlay "chrome" (top bar + close button).

    Attributes:
        chrome_bar_h_px:
            Height of the top bar area (in pixels). The bar sits *above* the inner selection
            rectangle, so it can host controls without covering the capture region.
        chrome_gap_px:
            Padding (in pixels) used around the close button inside the chrome bar.
        chrome_btn_pref_px:
            Preferred close button size (in pixels). Actual size is clamped to fit within the bar.
    """
    chrome_bar_h_px: int
    chrome_gap_px: int
    chrome_btn_pref_px: int


class ChromeUi:
    """
    Draw and hit-test the overlay chrome.

    This class is intentionally UI-only:
    - It does not own any QWidget state.
    - It provides pure geometry helpers (rects, sizing) and draw methods.

    State:
    - Tracks hover state for the close button so the caller can trigger repaints only when needed.
    """

    def __init__(self, cfg: ChromeConfig) -> None:
        """Initialize this object with the provided inputs and prepare its internal state."""
        self._cfg = cfg
        self._close_hover = False

    @property
    def close_hover(self) -> bool:
        """
        Whether the mouse is currently hovering over the close button.
        """
        return self._close_hover

    def btn_size_px(self) -> int:
        """
        Compute the close button size in pixels.

        Rules:
        - Must fit inside the chrome bar with `chrome_gap_px` padding on both sides.
        - Enforce a minimum size so the button remains usable on small bars.
        - Prefer `chrome_btn_pref_px` but clamp down if it would not fit.
        """
        max_s = max(14, int(self._cfg.chrome_bar_h_px) - 2 * int(self._cfg.chrome_gap_px))
        return int(min(int(self._cfg.chrome_btn_pref_px), max_s))

    def chrome_y(self, *, inner_top: int, s: int) -> int:
        """
        Compute the y-coordinate (top) for a control of size `s` inside the chrome bar.

        Coordinates:
        - `inner_top` is the top y-coordinate of the inner selection region.
        - The chrome bar occupies [inner_top - chrome_bar_h_px, inner_top).
        - Returned y is vertically centered within that bar.
        """
        bar_top = int(inner_top) - int(self._cfg.chrome_bar_h_px)
        return int(bar_top + max(0, (int(self._cfg.chrome_bar_h_px) - int(s)) // 2))

    def close_rect(self, *, widget_w: int, inner_top: int) -> QRect:
        """
        Return the QRect for the close button.

        Layout:
        - Right-aligned inside the chrome bar.
        - `chrome_gap_px` padding from the right edge.
        - Vertically centered in the chrome bar.
        """
        s = self.btn_size_px()
        g = int(self._cfg.chrome_gap_px)
        x = int(widget_w) - s - g
        y = self.chrome_y(inner_top=inner_top, s=s)
        return QRect(x, y, s, s)

    def update_hover(self, *, widget_w: int, inner_top: int, pos: QPoint) -> bool:
        """
        Update hover state based on the current mouse position.

        Returns:
            True if hover state changed (caller should repaint), else False.

        This method is used to avoid repainting on every mouse-move when hover state
        has not actually changed.
        """
        new_hover = self.close_rect(widget_w=widget_w, inner_top=inner_top).contains(pos)
        if new_hover == self._close_hover:
            return False
        self._close_hover = new_hover
        return True

    def draw_bar(self, p: QPainter, *, widget_w: int, inner_top: int) -> None:
        """
        Draw the top chrome bar.

        Visual:
        - Semi-transparent black overlay spanning the width of the widget.
        - Height extends from y=0 down to y=inner_top (so the bar sits above the selection area).

        Note:
        - Caller controls overall widget transparency and composition; we just paint shapes.
        """
        bar = QRect(0, 0, int(widget_w), int(inner_top))
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawRect(bar)
        p.restore()

    def draw_close_button(self, p: QPainter, *, widget_w: int, inner_top: int) -> None:
        """
        Draw the close button (red rounded square with an 'X').

        Hover behavior:
        - Increases alpha when hovered to provide clear affordance feedback.

        Geometry:
        - Corner radius scales with button size but is clamped to avoid extremes.
        - X padding scales with button size but is clamped to keep strokes inside bounds.
        """
        close_r = self.close_rect(widget_w=widget_w, inner_top=inner_top)
        s = max(1, close_r.width())
        radius = clamp_int(int(round(s * 0.18)), 3, 8)

        p.save()

        # Background pill.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(220, 30, 30, 240 if self._close_hover else 200))
        p.drawRoundedRect(close_r, radius, radius)

        # White "X" strokes.
        x_pen = QPen(QColor(255, 255, 255, 245))
        x_pen.setWidth(2)
        p.setPen(x_pen)

        pad = clamp_int(int(round(close_r.width() * 0.28)), 7, 12)
        p.drawLine(
            close_r.left() + pad,
            close_r.top() + pad,
            close_r.right() - pad,
            close_r.bottom() - pad,
        )
        p.drawLine(
            close_r.right() - pad,
            close_r.top() + pad,
            close_r.left() + pad,
            close_r.bottom() - pad,
        )

        p.restore()
