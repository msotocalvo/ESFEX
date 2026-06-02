"""Simple undo/redo stack using pickle snapshots."""

from __future__ import annotations

import pickle
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import GuiSystemState

_PICKLE_PROTOCOL = pickle.HIGHEST_PROTOCOL


class UndoStack:
    """Snapshot-based undo/redo for the GUI state.

    Snapshots are stored as pickle byte blobs — serialization is ~4×
    faster than ``copy.deepcopy`` and deserialization only happens on
    undo/redo (which is infrequent).
    """

    def __init__(self, max_depth: int = 50):
        self._stack: list[bytes] = []
        self._redo: list[bytes] = []
        self._max = max_depth

    def push(self, state: "GuiSystemState"):
        """Save a snapshot of the current state."""
        self._stack.append(pickle.dumps(state, _PICKLE_PROTOCOL))
        if len(self._stack) > self._max:
            self._stack.pop(0)
        self._redo.clear()

    def undo(self, current: "GuiSystemState") -> "GuiSystemState | None":
        """Undo to the previous state, returning it (or None if empty)."""
        if not self._stack:
            return None
        self._redo.append(pickle.dumps(current, _PICKLE_PROTOCOL))
        return pickle.loads(self._stack.pop())

    def redo(self, current: "GuiSystemState") -> "GuiSystemState | None":
        """Redo to the next state, returning it (or None if empty)."""
        if not self._redo:
            return None
        self._stack.append(pickle.dumps(current, _PICKLE_PROTOCOL))
        return pickle.loads(self._redo.pop())

    @property
    def can_undo(self) -> bool:
        return len(self._stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo) > 0
