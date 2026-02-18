# ui/window_coupler.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class CouplerConfig:
    sync_move: bool = True
    sync_resize: bool = True


class WindowCoupler(QObject):
    def __init__(self, *, a: QWidget, b: QWidget, cfg: CouplerConfig = CouplerConfig()) -> None:
        super().__init__()
        self._a = a
        self._b = b
        self._cfg = cfg
        self._in_sync: bool = False

        self._a.installEventFilter(self)
        self._b.installEventFilter(self)

        # Best-effort cleanup: remove filters if either window is destroyed.
        self._a.destroyed.connect(lambda _=None: self._detach())  # type: ignore[arg-type]
        self._b.destroyed.connect(lambda _=None: self._detach())  # type: ignore[arg-type]

    def _detach(self) -> None:
        try:
            self._a.removeEventFilter(self)
        except Exception:
            pass
        try:
            self._b.removeEventFilter(self)
        except Exception:
            pass

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if self._in_sync:
            return False

        et = event.type()
        if et not in (QEvent.Type.Move, QEvent.Type.Resize):
            return False

        if et == QEvent.Type.Move and not self._cfg.sync_move:
            return False
        if et == QEvent.Type.Resize and not self._cfg.sync_resize:
            return False

        src: Optional[QWidget] = watched if isinstance(watched, QWidget) else None
        if src is None:
            return False

        if src is self._a:
            dst = self._b
        elif src is self._b:
            dst = self._a
        else:
            return False

        try:
            self._in_sync = True
            dst.setGeometry(src.geometry())
        finally:
            self._in_sync = False

        return False
