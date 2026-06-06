"""System deletion regressions (#8).

The Grid Builder renames every system it creates, so the element tree must keep
the renamed item's stored id in sync — otherwise the delete context menu emits
the stale *old* name, ``_on_delete_system`` hits a missing key (KeyError) and
aborts, and the system stays in the tree.

These tests exercise the element-tree id-sync directly (a lightweight widget,
no full MainWindow, to stay isolated from the rest of the GUI suite). The
save-side half of the bug — a deleted system surviving in the config and
reappearing on reload — is covered by
``test_serializer.TestDeletedSystemPruning``.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tree(qapp):
    from esfex.visualization.panels.element_tree import ElementTreePanel
    w = ElementTreePanel()
    try:
        yield w
    finally:
        w.deleteLater()


def test_add_system_stores_id(tree):
    tree.add_system("A")
    assert tree._system_items["A"].data(0, 100) == ("system", "A")


def test_rename_keeps_item_id_in_sync(tree):
    """A renamed system's stored id must follow the new name.

    The delete context menu reads ``data(0, 100)``; if the rename leaves it on
    the old name, deletion targets a missing key and silently fails.
    """
    tree.add_system("A")
    tree.rename_system("A", "Region1")
    assert "A" not in tree._system_items
    item = tree._system_items["Region1"]
    assert item.data(0, 100) == ("system", "Region1")


def test_remove_renamed_system_by_current_id(tree):
    """Removing a renamed system by its (correct) new id prunes the tree item."""
    tree.add_system("A")
    tree.add_system("Keep")
    tree.rename_system("A", "Region1")
    eid = tree._system_items["Region1"].data(0, 100)[1]
    assert eid == "Region1"
    tree.remove_system(eid)
    assert "Region1" not in tree._system_items
    assert "Keep" in tree._system_items
