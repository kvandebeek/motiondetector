# ui/selector_paint.py
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen

from ui.selector.chrome import ChromeUi
from ui.selector.models import clamp_int


@dataclass(frozen=True)
class PaintConfig:
    """
    Rendering configuration for the selector overlay.

    Geometry:
    - border_px: thickness of the inner region border (around the selectable capture area).
    - grid_line_px: thickness of grid lines drawn inside the region.
    - grid_rows/grid_cols: grid resolution (must match GridGeometry / tile mapping).

    Styling:
    - tile_label_fg/bg: text + background colors for tile number badges.
    - disabled_fill: semi-transparent fill used to indicate a tile is disabled.
    - disabled_x_pen: pen used to draw an "X" over disabled tiles.
    """
    border_px: int
    grid_line_px: int
    grid_rows: int
    grid_cols: int
    tile_label_fg: QColor
    tile_label_bg: QColor
    disabled_fill: QColor
    disabled_x_pen: QPen


class SelectorPainter:
    """
    Paints the selector overlay contents.

    Responsibilities:
    - Draw the chrome/top bar and close button (delegated to ChromeUi).
    - Draw the inner capture border.
    - Draw dashed grid lines.
    - Draw per-tile overlays for disabled tiles.
    - Optionally draw tile number badges centered within each tile.

    Inputs to paint() are intentionally precomputed:
    - inner: the inner rect (capture area) computed by GridGeometry.
    - x_edges/y_edges: cumulative edge offsets for tile boundaries (length cols+1 / rows+1),
      so painting does not need to recompute layout logic.
    """

    def __init__(self, *, cfg: PaintConfig, chrome: ChromeUi) -> None:
        self._cfg = cfg
        self._chrome = chrome

    def _tile_font(self, *, tile_h: int) -> QFont:
        """
        Choose a bold font sized proportionally to tile height.

        The clamp avoids:
        - too small (unreadable) labels on small regions
        - overly large labels that dominate the tile on large regions
        """
        px = clamp_int(int(round(tile_h * 0.24)), 10, 32)
        f = QFont()
        f.setPixelSize(px)
        f.setBold(True)
        return f

    def _draw_centered_tile_label(self, p: QPainter, *, tile: QRect, label: str) -> None:
        """
        Draw a rounded-rect badge centered within a tile, then draw the label centered in the badge.

        The badge size is derived from:
        - label text metrics (QFontMetrics)
        - padding scaled to tile size
        and then clamped to never exceed the tile itself.
        """
        fm = QFontMetrics(p.font())
        tw = fm.horizontalAdvance(label)
        th = fm.height()

        pad = clamp_int(int(round(min(tile.width(), tile.height()) * 0.06)), 4, 10)
        bw = min(tile.width(), tw + 2 * pad)
        bh = min(tile.height(), th + 2 * pad)

        bx = tile.left() + max(0, (tile.width() - bw) // 2)
        by = tile.top() + max(0, (tile.height() - bh) // 2)
        bg = QRect(bx, by, bw, bh)

        # Badge background (no outline).
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._cfg.tile_label_bg)
        radius = clamp_int(int(round(min(bg.width(), bg.height()) * 0.22)), 4, 12)
        p.drawRoundedRect(bg, radius, radius)

        # Label text.
        p.setPen(self._cfg.tile_label_fg)
        p.drawText(bg, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_disabled_overlay(self, p: QPainter, tile: QRect) -> None:
        """
        Draw a disabled mask over a tile:
        - translucent fill
        - a prominent "X" using disabled_x_pen

        p.save()/restore() keeps any pen/brush changes local to this overlay.
        """
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._cfg.disabled_fill)
        p.drawRect(tile)

        p.setPen(self._cfg.disabled_x_pen)
        pad = clamp_int(int(round(min(tile.width(), tile.height()) * 0.08)), 6, 14)
        p.drawLine(tile.left() + pad, tile.top() + pad, tile.right() - pad, tile.bottom() - pad)
        p.drawLine(tile.right() - pad, tile.top() + pad, tile.left() + pad, tile.bottom() - pad)
        p.restore()

    def paint(
        self,
        p: QPainter,
        *,
        widget_w: int,
        widget_h: int,
        inner: QRect,
        x_edges: list[int],
        y_edges: list[int],
        show_tile_numbers: bool,
        disabled_tiles: set[int],
        show_overlay_state: bool,
        current_state: str,
    ) -> None:
        """
        Paint the entire selector overlay.

        Args:
            p: active QPainter.
            widget_w/widget_h: widget size in logical pixels (widget_h not used currently, but kept for symmetry).
            inner: inner rect representing the capture region area.
            x_edges/y_edges: tile boundary offsets relative to inner's left/top:
              - x_edges length == grid_cols + 1; x_edges[0]==0, x_edges[-1]==inner.width()
              - y_edges length == grid_rows + 1; y_edges[0]==0, y_edges[-1]==inner.height()
            show_tile_numbers: whether to draw tile index badges.
            disabled_tiles: set of tile indices (0-based) that should be masked with an "X".
        """
        # Antialiasing improves rounded badges and diagonal "X" lines.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Top chrome bar (title area / background).
        self._chrome.draw_bar(p, widget_w=widget_w, inner_top=inner.top())


        if show_overlay_state:
            p.save()
            p.setPen(QColor(255, 255, 255, 230))
            f = QFont()
            f.setPixelSize(14)
            f.setBold(True)
            p.setFont(f)
            label = f"STATE: {str(current_state or 'UNKNOWN')}"
            p.drawText(QRect(10, 0, max(1, widget_w - 80), max(1, inner.top())), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
            p.restore()

        # Border around the inner region.
        pen = QPen(Qt.GlobalColor.cyan)
        pen.setWidth(int(self._cfg.border_px))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(inner)

        # Grid lines (dashed).
        pen2 = QPen(Qt.GlobalColor.cyan)
        pen2.setWidth(int(self._cfg.grid_line_px))
        pen2.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen2)

        # Cache rect edges to avoid repeated Qt calls in loops.
        left = inner.left()
        top = inner.top()
        right = inner.right()
        bottom = inner.bottom()

        # Vertical grid lines at each internal column boundary (skip 0 and last edge).
        for i in range(1, int(self._cfg.grid_cols)):
            x = left + x_edges[i]
            p.drawLine(x, top, x, bottom)

        # Horizontal grid lines at each internal row boundary.
        for i in range(1, int(self._cfg.grid_rows)):
            y = top + y_edges[i]
            p.drawLine(left, y, right, y)

        # Disabled overlays per tile.
        # Tile rects are built from x_edges/y_edges and then adjusted(-1,-1) on bottom/right
        # to avoid overpainting the next tile boundary due to inclusive QRect edges.
        for row in range(int(self._cfg.grid_rows)):
            y0 = top + y_edges[row]
            y1 = top + y_edges[row + 1]
            for col in range(int(self._cfg.grid_cols)):
                x0 = left + x_edges[col]
                x1 = left + x_edges[col + 1]
                tile = QRect(x0, y0, max(1, x1 - x0), max(1, y1 - y0)).adjusted(0, 0, -1, -1)
                idx0 = row * int(self._cfg.grid_cols) + col
                if idx0 in disabled_tiles:
                    self._draw_disabled_overlay(p, tile)

        # Tile numbers (1-based labels for user readability).
        if show_tile_numbers:
            # Font size is tied to tile height (inner height divided by rows).
            p.setFont(self._tile_font(tile_h=max(1, inner.height() // int(self._cfg.grid_rows))))
            for row in range(int(self._cfg.grid_rows)):
                y0 = top + y_edges[row]
                y1 = top + y_edges[row + 1]
                for col in range(int(self._cfg.grid_cols)):
                    x0 = left + x_edges[col]
                    x1 = left + x_edges[col + 1]
                    tile = QRect(x0, y0, max(1, x1 - x0), max(1, y1 - y0)).adjusted(0, 0, -1, -1)
                    self._draw_centered_tile_label(
                        p,
                        tile=tile,
                        label=str(row * int(self._cfg.grid_cols) + col + 1),
                    )

        # Close button on top of everything (so it remains visible).
        self._chrome.draw_close_button(p, widget_w=widget_w, inner_top=inner.top())
