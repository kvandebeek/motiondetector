# ui/selector_interaction.py
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QWidget

from ui.selector.chrome import ChromeUi
from ui.selector.grid import GridGeometry
from ui.selector.region_emit import RegionEmitter
from ui.tiles_sync import TilesSync
from ui.selector.models import ResizeMode


@dataclass(frozen=True)
class InteractionConfig:
    """
    Tuning knobs for mouse interaction behavior.

    - margin_px: hit-test band (in pixels) near the window edges used for resize detection.
    - min_w/min_h: minimum overlay window size enforced during resizing.
    """
    margin_px: int = 12
    min_w: int = 120
    min_h: int = 90


class SelectorInteractor:
    """
    Centralizes all pointer interaction logic for the selector overlay widget.

    Responsibilities:
    - Hover: update Chrome (e.g., close button hover) and set correct cursor shape.
    - Hit-testing: decide whether a pointer position implies moving vs resizing (and which edge/corner).
    - Click routing:
      - Close button -> request close.
      - Title/chrome area (above inner region) -> move the window.
      - Inside grid area -> toggle tile selection when clicking on a tile (mode == "move").
      - On edges/corners -> resize.
    - Dragging: apply move/resize geometry updates and notify RegionEmitter.

    Design note:
    - Uses global mouse deltas (global_pos - drag_start_pos) so moving is stable regardless of widget-local coords.
    - Emits region updates during drag for real-time feedback, and once again on release to finalize.
    """

    def __init__(
        self,
        *,
        widget: QWidget,
        grid: GridGeometry,
        chrome: ChromeUi,
        tiles: TilesSync,
        region_emitter: RegionEmitter,
        on_close: callable,
        cfg: InteractionConfig = InteractionConfig(),
    ) -> None:
        # The actual overlay widget whose geometry we manipulate.
        self._w = widget

        # Grid geometry helper:
        # - defines the "inner rect" (area under chrome/top bar) and tile hit-testing.
        self._grid = grid

        # Chrome UI helper:
        # - provides close button rect and hover state.
        self._chrome = chrome

        # Sync object for toggling which tiles are active/disabled/etc.
        self._tiles = tiles

        # Responsible for converting widget geometry into a Region and emitting it to listeners.
        self._region = region_emitter

        # Callback invoked when close is requested.
        self._on_close = on_close

        # Interaction config (hit-test margin, min size).
        self._cfg = cfg

        # Current drag mode:
        # - "none" means not dragging
        # - "move" means moving window or clicking tile (depending on where press occurred)
        # - edge/corner codes (l/r/t/b/tl/tr/bl/br) mean resizing.
        self._drag_mode: ResizeMode = "none"

        # Global cursor position at drag start (screen coordinates).
        self._drag_start_pos = QPoint(0, 0)

        # Widget geometry at drag start (screen coordinates).
        self._start_geom = QRect()

    def update_hover(self, pos: QPoint) -> bool:
        """
        Update chrome hover state (notably the close button) for the given local widget position.

        Returns:
            bool: whether hover state changed (delegated to ChromeUi).
        """
        inner_top = self._grid.inner_rect(self._w.rect()).top()
        return self._chrome.update_hover(widget_w=self._w.width(), inner_top=inner_top, pos=pos)

    def hit_test(self, pos: QPoint) -> ResizeMode:
        """
        Determine what interaction mode a local pointer position implies.

        Priority:
        1) Close button region: treated as "move" here (cursor/press logic special-cases it).
        2) Above inner rect (chrome/title bar): "move"
        3) Near edges/corners within margin_px: resize mode
        4) Everywhere else inside inner area: "move" (used for tile toggles or move fallback)
        """
        inner_top = self._grid.inner_rect(self._w.rect()).top()

        # Special-case: if hovering the close button, we don't want resize cursors.
        # We return "move" so set_cursor_for can decide pointing-hand via chrome.close_hover.
        if self._chrome.close_rect(widget_w=self._w.width(), inner_top=inner_top).contains(pos):
            return "move"

        # Edge/corner hit-testing in widget-local coordinates.
        m = int(self._cfg.margin_px)
        x = pos.x()
        y = pos.y()
        w = self._w.width()
        h = self._w.height()

        left = x <= m
        right = x >= w - m
        top = y <= m
        bottom = y >= h - m

        # Corners first (more specific).
        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"

        # Then edges.
        if left:
            return "l"
        if right:
            return "r"
        if top:
            return "t"
        if bottom:
            return "b"

        # Top chrome area (above the grid/inner rect) but away from edge handles moves the window.
        if pos.y() < inner_top:
            return "move"

        # Inside: default behavior is "move" (but press logic may interpret as tile toggle).
        return "move"

    def set_cursor_for(self, *, pos: QPoint) -> None:
        """
        Set cursor shape based on hover position and current state.

        Rules:
        - While dragging, we do not change cursor mid-drag (keeps UX stable).
        - If close button hover is active -> pointing hand.
        - If pointer is in chrome/title area -> move cursor.
        - Else -> resize cursor for edges/corners, otherwise move cursor.
        """
        inner_top = self._grid.inner_rect(self._w.rect()).top()
        mode = self.hit_test(pos)

        # Do not override cursor while dragging; mouse move should not fight drag mode.
        if self._drag_mode != "none":
            return

        # Chrome can decide hover, we map that to a pointing-hand cursor.
        if self._chrome.close_hover:
            self._w.setCursor(Qt.CursorShape.PointingHandCursor)
            return

        # Above inner rect is "move window".
        if pos.y() < inner_top:
            self._w.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        # Resize cursors depend on which edge/corner we're on.
        if mode in ("l", "r"):
            self._w.setCursor(Qt.CursorShape.SizeHorCursor)
        elif mode in ("t", "b"):
            self._w.setCursor(Qt.CursorShape.SizeVerCursor)
        elif mode in ("tl", "br"):
            self._w.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif mode in ("tr", "bl"):
            self._w.setCursor(Qt.CursorShape.SizeBDiagCursor)
        else:
            self._w.setCursor(Qt.CursorShape.SizeAllCursor)

    def on_mouse_press(self, *, button: Qt.MouseButton, pos: QPoint, global_pos: QPoint) -> bool:
        """
        Handle mouse press.

        Returns:
            bool: True if the event was handled and should not propagate further.

        Behavior:
        - Left click only.
        - If close button was clicked -> request close immediately.
        - If clicked in chrome/title area -> begin move drag.
        - If clicked inside inner area:
          - If not on resize edges/corners (mode == "move") and on a tile -> toggle that tile.
          - Otherwise -> begin resize drag for the detected mode.
        """
        if button != Qt.MouseButton.LeftButton:
            return False

        inner = self._grid.inner_rect(self._w.rect())
        close_r = self._chrome.close_rect(widget_w=self._w.width(), inner_top=inner.top())

        # Close button wins; no drag should start.
        if close_r.contains(pos):
            self._on_close()
            return True

        # Chrome/title bar drag: move window.
        if pos.y() < inner.top():
            self._drag_mode = "move"
            self._drag_start_pos = global_pos
            self._start_geom = self._w.geometry()
            return True

        mode = self.hit_test(pos)

        # Clicking inside the grid area with "move" means "toggle tile" if over a tile.
        # This intentionally prevents starting a move drag from inside the grid; the grid is interactive.
        if mode == "move":
            idx = self._grid.tile_index_at(widget_rect=self._w.rect(), pos=pos)
            if idx is not None:
                self._tiles.toggle(idx)
                return True

        # Otherwise start a drag (move/resize depending on mode).
        self._drag_mode = mode
        self._drag_start_pos = global_pos
        self._start_geom = self._w.geometry()
        return True

    def on_mouse_move(self, *, pos: QPoint, global_pos: QPoint) -> bool:
        """
        Handle mouse move during an active drag.

        Returns:
            bool: True if dragging was in progress and we updated geometry.

        Implementation details:
        - We clone the start geometry and apply delta from the original global press position.
        - Resize adjusts the corresponding edges; then we clamp to minimum size.
        - After applying geometry, emit region updates with reason="drag" for live monitoring.
        """
        if self._drag_mode == "none":
            return False

        delta = global_pos - self._drag_start_pos
        g = QRect(self._start_geom)

        min_w = int(self._cfg.min_w)
        min_h = int(self._cfg.min_h)

        if self._drag_mode == "move":
            # Move uses the start top-left plus global delta.
            g.moveTo(self._start_geom.topLeft() + delta)
        else:
            dx = delta.x()
            dy = delta.y()

            # Resizing: mutate only the edges implied by drag mode.
            if "l" in self._drag_mode:
                g.setLeft(g.left() + dx)
            if "r" in self._drag_mode:
                g.setRight(g.right() + dx)
            if "t" in self._drag_mode:
                g.setTop(g.top() + dy)
            if "b" in self._drag_mode:
                g.setBottom(g.bottom() + dy)

            # Enforce min width by re-adjusting the dragged edge.
            if g.width() < min_w:
                if "l" in self._drag_mode:
                    g.setLeft(g.right() - min_w)
                else:
                    g.setRight(g.left() + min_w)

            # Enforce min height by re-adjusting the dragged edge.
            if g.height() < min_h:
                if "t" in self._drag_mode:
                    g.setTop(g.bottom() - min_h)
                else:
                    g.setBottom(g.top() + min_h)

        # Apply to widget and notify region listeners continuously.
        self._w.setGeometry(g)
        self._region.emit(reason="drag")
        return True

    def on_mouse_release(self) -> None:
        """
        Finish any active drag and emit a final region update.

        The extra emit on release is useful to:
        - ensure any consumers see a "final" region even if they debounce drag events,
        - provide a clean boundary for "user finished interaction".
        """
        self._drag_mode = "none"
        self._region.emit(reason="release")

    def close_requested(self, *, pos: QPoint) -> bool:
        """
        Utility used by callers to check whether a local position is within the close button.

        This is separate from on_mouse_press to allow:
        - higher-level event filters to pre-check close intent,
        - keyboard / other input paths to share the same hit-test.
        """
        inner_top = self._grid.inner_rect(self._w.rect()).top()
        return self._chrome.close_rect(widget_w=self._w.width(), inner_top=inner_top).contains(pos)
