"""Tests for the UndoStack class.

We use a simple FakeState dataclass as a stand-in for GuiSystemState
to avoid importing the full gui_model module (which depends on Qt).
UndoStack only relies on ``copy.deepcopy``, so any deepcopy-able object
works.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import pytest

from esfex.visualization.data.undo import UndoStack


# ---------------------------------------------------------------------------
# Stand-in state object
# ---------------------------------------------------------------------------


@dataclass
class FakeState:
    """Minimal deepcopy-able state used instead of GuiSystemState."""

    value: int = 0
    items: list[int] = field(default_factory=list)


# ===========================================================================
# Empty stack
# ===========================================================================


class TestEmptyStack:
    """Behaviour of a freshly-created, empty UndoStack."""

    def test_can_undo_is_false(self):
        stack = UndoStack()
        assert stack.can_undo is False

    def test_can_redo_is_false(self):
        stack = UndoStack()
        assert stack.can_redo is False

    def test_undo_returns_none(self):
        stack = UndoStack()
        current = FakeState(value=1)
        assert stack.undo(current) is None

    def test_redo_returns_none(self):
        stack = UndoStack()
        current = FakeState(value=1)
        assert stack.redo(current) is None

    def test_undo_does_not_change_can_redo(self):
        stack = UndoStack()
        stack.undo(FakeState(value=0))
        assert stack.can_redo is False


# ===========================================================================
# Push + Undo
# ===========================================================================


class TestPushAndUndo:
    """Push a snapshot, then undo."""

    def test_push_makes_can_undo_true(self):
        stack = UndoStack()
        stack.push(FakeState(value=10))
        assert stack.can_undo is True

    def test_push_keeps_can_redo_false(self):
        stack = UndoStack()
        stack.push(FakeState(value=10))
        assert stack.can_redo is False

    def test_undo_returns_pushed_state(self):
        stack = UndoStack()
        stack.push(FakeState(value=10))
        result = stack.undo(FakeState(value=20))
        assert result is not None
        assert result.value == 10

    def test_undo_empties_stack(self):
        stack = UndoStack()
        stack.push(FakeState(value=10))
        stack.undo(FakeState(value=20))
        assert stack.can_undo is False

    def test_undo_enables_redo(self):
        stack = UndoStack()
        stack.push(FakeState(value=10))
        stack.undo(FakeState(value=20))
        assert stack.can_redo is True


# ===========================================================================
# Push + Undo + Redo (full cycle)
# ===========================================================================


class TestFullCycle:
    """Push -> Undo -> Redo cycle."""

    def test_redo_returns_current_at_undo_time(self):
        stack = UndoStack()
        state_a = FakeState(value=1)
        state_b = FakeState(value=2)

        stack.push(state_a)
        # current is state_b when we undo
        undone = stack.undo(state_b)
        assert undone is not None
        assert undone.value == 1

        # redo should give back state_b (the current at undo time)
        redone = stack.redo(FakeState(value=3))
        assert redone is not None
        assert redone.value == 2

    def test_redo_enables_can_undo_again(self):
        stack = UndoStack()
        stack.push(FakeState(value=1))
        stack.undo(FakeState(value=2))
        assert stack.can_undo is False
        stack.redo(FakeState(value=3))
        assert stack.can_undo is True

    def test_redo_clears_can_redo(self):
        stack = UndoStack()
        stack.push(FakeState(value=1))
        stack.undo(FakeState(value=2))
        stack.redo(FakeState(value=3))
        assert stack.can_redo is False

    def test_double_undo_double_redo(self):
        stack = UndoStack()
        stack.push(FakeState(value=1))
        stack.push(FakeState(value=2))

        # undo twice
        u1 = stack.undo(FakeState(value=3))
        assert u1 is not None and u1.value == 2
        u2 = stack.undo(FakeState(value=99))
        assert u2 is not None and u2.value == 1

        # redo twice
        r1 = stack.redo(FakeState(value=100))
        assert r1 is not None and r1.value == 99
        r2 = stack.redo(FakeState(value=200))
        assert r2 is not None and r2.value == 3


# ===========================================================================
# Multiple pushes (LIFO order)
# ===========================================================================


class TestMultiplePushes:
    """Undo must return items in LIFO order."""

    def test_lifo_order_three_items(self):
        stack = UndoStack()
        stack.push(FakeState(value=1))
        stack.push(FakeState(value=2))
        stack.push(FakeState(value=3))

        u1 = stack.undo(FakeState(value=99))
        assert u1 is not None and u1.value == 3
        u2 = stack.undo(FakeState(value=99))
        assert u2 is not None and u2.value == 2
        u3 = stack.undo(FakeState(value=99))
        assert u3 is not None and u3.value == 1
        u4 = stack.undo(FakeState(value=99))
        assert u4 is None

    def test_lifo_order_five_items(self):
        stack = UndoStack()
        for i in range(5):
            stack.push(FakeState(value=i))
        for i in range(4, -1, -1):
            result = stack.undo(FakeState(value=-1))
            assert result is not None
            assert result.value == i

    def test_interleaved_push_undo(self):
        stack = UndoStack()
        stack.push(FakeState(value=1))
        stack.push(FakeState(value=2))
        u = stack.undo(FakeState(value=99))
        assert u is not None and u.value == 2
        stack.push(FakeState(value=3))
        u = stack.undo(FakeState(value=99))
        assert u is not None and u.value == 3


# ===========================================================================
# Max depth
# ===========================================================================


class TestMaxDepth:
    """Oldest items should be dropped when exceeding max_depth."""

    def test_max_depth_drops_oldest(self):
        stack = UndoStack(max_depth=3)
        for i in range(5):
            stack.push(FakeState(value=i))

        # Only last 3 should remain: values 2, 3, 4
        u1 = stack.undo(FakeState(value=-1))
        assert u1 is not None and u1.value == 4
        u2 = stack.undo(FakeState(value=-1))
        assert u2 is not None and u2.value == 3
        u3 = stack.undo(FakeState(value=-1))
        assert u3 is not None and u3.value == 2
        u4 = stack.undo(FakeState(value=-1))
        assert u4 is None

    def test_max_depth_of_one(self):
        stack = UndoStack(max_depth=1)
        stack.push(FakeState(value=10))
        stack.push(FakeState(value=20))
        u = stack.undo(FakeState(value=-1))
        assert u is not None and u.value == 20
        assert stack.undo(FakeState(value=-1)) is None

    def test_default_max_depth_is_50(self):
        stack = UndoStack()
        assert stack._max == 50

    def test_exact_max_depth_keeps_all(self):
        stack = UndoStack(max_depth=3)
        stack.push(FakeState(value=1))
        stack.push(FakeState(value=2))
        stack.push(FakeState(value=3))
        # All 3 should be present
        u1 = stack.undo(FakeState(value=-1))
        assert u1 is not None and u1.value == 3
        u2 = stack.undo(FakeState(value=-1))
        assert u2 is not None and u2.value == 2
        u3 = stack.undo(FakeState(value=-1))
        assert u3 is not None and u3.value == 1


# ===========================================================================
# Push clears redo stack
# ===========================================================================


class TestPushClearsRedo:
    """After push, redo history must be discarded."""

    def test_push_after_undo_clears_redo(self):
        stack = UndoStack()
        stack.push(FakeState(value=1))
        stack.undo(FakeState(value=2))
        assert stack.can_redo is True

        stack.push(FakeState(value=3))
        assert stack.can_redo is False

    def test_push_after_undo_redo_not_available(self):
        stack = UndoStack()
        stack.push(FakeState(value=1))
        stack.push(FakeState(value=2))
        stack.undo(FakeState(value=3))
        stack.undo(FakeState(value=4))
        assert stack.can_redo is True

        stack.push(FakeState(value=5))
        assert stack.can_redo is False
        assert stack.redo(FakeState(value=99)) is None


# ===========================================================================
# Deep copy isolation
# ===========================================================================


class TestDeepCopyIsolation:
    """Modifying the original after push must not affect stored snapshot."""

    def test_mutating_state_after_push(self):
        state = FakeState(value=10, items=[1, 2, 3])
        stack = UndoStack()
        stack.push(state)

        # Mutate the original
        state.value = 999
        state.items.append(4)

        restored = stack.undo(FakeState(value=0))
        assert restored is not None
        assert restored.value == 10
        assert restored.items == [1, 2, 3]

    def test_mutating_undo_result_does_not_affect_redo(self):
        stack = UndoStack()
        stack.push(FakeState(value=10, items=[1]))
        result = stack.undo(FakeState(value=20, items=[2]))
        assert result is not None

        # Mutate the undo result
        result.value = 999
        result.items.append(99)

        # Redo should give back original current (value=20)
        redone = stack.redo(FakeState(value=30))
        assert redone is not None
        assert redone.value == 20
        assert redone.items == [2]

    def test_pushed_state_is_not_same_object(self):
        state = FakeState(value=5)
        stack = UndoStack()
        stack.push(state)
        restored = stack.undo(FakeState(value=0))
        assert restored is not None
        assert restored is not state

    def test_undo_current_is_deep_copied_for_redo(self):
        stack = UndoStack()
        stack.push(FakeState(value=1))
        current = FakeState(value=2, items=[10, 20])
        stack.undo(current)

        # Mutate current after undo
        current.value = 999
        current.items.clear()

        # Redo should return the original snapshot of current
        redone = stack.redo(FakeState(value=0))
        assert redone is not None
        assert redone.value == 2
        assert redone.items == [10, 20]
