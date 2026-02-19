# File commentary: ui/selector/state.py - This file holds logic used by the motion detector project.
# ui/selector_state.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPoint, QRect

from ui.selector.models import ResizeMode


@dataclass
class DragState:
    """
    Tracks an active pointer drag interaction.

    Fields:
    - mode: current drag mode ("none" when idle). When dragging, this captures whether the user
      is moving the window ("move") or resizing via a specific edge/corner ("l", "tr", etc.).
    - start_pos_global: global (screen) mouse position at the moment the drag began.
      Using global coordinates keeps dragging stable across widget-local coordinate changes.
    - start_geom: widget geometry snapshot at drag start. All drag deltas are applied relative
      to this baseline geometry.
    """
    mode: ResizeMode = "none"
    start_pos_global: QPoint = QPoint(0, 0)
    start_geom: QRect = QRect()


@dataclass
class SelectorVisualState:
    """
    Aggregated UI state used by the selector overlay for rendering and interaction.

    This is intentionally "visual/controller" state (not capture state):
    - show_tile_numbers drives whether tile indices are painted.
    - drag holds the active drag session state.
    - min_w/min_h are UI constraints used when resizing.
    - last_cursor_mode can be used to avoid redundant cursor updates or to
      debug cursor/hit-test behavior.

    Note:
    - drag defaults to a fresh DragState so callers can mutate drag.mode/start_* during interactions.
    """
    show_tile_numbers: bool
    drag: DragState = DragState()
    min_w: int = 120
    min_h: int = 90
    last_cursor_mode: Optional[ResizeMode] = None
