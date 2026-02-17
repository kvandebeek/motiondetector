# ui/selector_grid.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPoint, QRect


@dataclass(frozen=True)
class GridGeometry:
    """
    Geometry helper for mapping between:
    - the selector widget rectangle (full overlay window)
    - the "inner" capture rectangle (excluding borders/insets/chrome)
    - tile grid edges and tile indices (row-major)

    This is used both for drawing (grid lines / tile overlays) and for hit-testing
    (e.g., clicking on a tile to toggle disabled state).
    """
    grid_rows: int
    grid_cols: int

    # Border thickness drawn around the inner capture rectangle.
    border_px: int

    # Extra inset applied before emitting the capture region (keeps analysis away from borders).
    emit_inset_px: int

    # Height of the top chrome bar (UI controls area) that should not be part of capture.
    chrome_bar_h_px: int

    def inner_rect(self, widget_rect: QRect) -> QRect:
        """
        Compute the inner capture rectangle within the full widget rectangle.

        Insets applied:
        - Left/right/bottom: border_px + emit_inset_px
        - Top: border_px + emit_inset_px + chrome_bar_h_px
          (because the chrome bar sits above the capture region)

        Returns:
            QRect describing the capture area in widget coordinates.

        Fallback:
            If the computed rect would be empty (too small), return a safe non-empty rect
            covering the widget. This avoids downstream divisions by zero and keeps
            drawing/hit-testing stable even under extreme resizing.
        """
        inset = max(0, int(self.border_px) + int(self.emit_inset_px))
        r = widget_rect.adjusted(inset, inset + int(self.chrome_bar_h_px), -inset, -inset)
        if r.width() < 1 or r.height() < 1:
            return QRect(0, 0, max(1, widget_rect.width()), max(1, widget_rect.height()))
        return r

    @staticmethod
    def edges(size: int, parts: int) -> list[int]:
        """
        Compute `parts` partitions of a 1D span and return the edge coordinates.

        Returns:
            List[int] of length parts+1 with:
              - out[0] == 0
              - out[parts] == size
              - intermediate edges proportional to i/parts (rounded)

        Notes:
            - Rounding distributes remainder across edges which prevents the classic
              "last column is wider" effect from pure integer division.
            - This does not enforce strict monotonicity under all rounding scenarios,
              but for typical UI sizes it is sufficient. If needed, add a fix-up pass
              to ensure out[i] >= out[i-1].
        """
        out = [int(round(i * size / parts)) for i in range(parts + 1)]
        out[0] = 0
        out[parts] = int(size)
        return out

    def tile_rects(self, *, widget_rect: QRect) -> tuple[QRect, list[int], list[int]]:
        """
        Compute the grid edge arrays for the current widget size.

        Returns:
            (inner_rect, x_edges, y_edges)
            - inner_rect is the drawable/capturable region
            - x_edges splits inner_rect.width() into grid_cols columns
            - y_edges splits inner_rect.height() into grid_rows rows

        The edges are relative to the inner_rect origin (0..width / 0..height).
        """
        inner = self.inner_rect(widget_rect)
        x_edges = self.edges(inner.width(), int(self.grid_cols))
        y_edges = self.edges(inner.height(), int(self.grid_rows))
        return inner, x_edges, y_edges

    def tile_index_at(self, *, widget_rect: QRect, pos: QPoint) -> Optional[int]:
        """
        Return the row-major tile index at the given widget position.

        Args:
            widget_rect: Full selector widget bounds.
            pos: Mouse position in widget coordinates.

        Returns:
            0-based tile index (row-major) if inside the inner rect, else None.

        Implementation details:
            - Convert position to coordinates relative to inner_rect.
            - Find the first edge interval that contains the point.
            - Fallback to the last row/col if rounding creates a boundary case.
        """
        inner, x_edges, y_edges = self.tile_rects(widget_rect=widget_rect)
        if not inner.contains(pos):
            return None

        rel_x = pos.x() - inner.left()
        rel_y = pos.y() - inner.top()

        cols = int(self.grid_cols)
        rows = int(self.grid_rows)

        col = next((c for c in range(cols) if x_edges[c] <= rel_x < x_edges[c + 1]), cols - 1)
        row = next((r for r in range(rows) if y_edges[r] <= rel_y < y_edges[r + 1]), rows - 1)

        return row * cols + col
