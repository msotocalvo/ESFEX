"""`_grow_dialog_area` enlarges a dialog's area by the requested factor."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QInputDialog

from esfex.visualization.main_window import _grow_dialog_area


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _input_dialog():
    d = QInputDialog()
    d.setInputMode(QInputDialog.TextInput)
    d.setWindowTitle("New System")
    d.setLabelText("Enter a name for the new system:")
    d.adjustSize()
    return d


def test_grows_area_by_twenty_percent(qapp):
    d = _input_dialog()
    base = d.sizeHint()
    base_area = base.width() * base.height()

    _grow_dialog_area(d, 1.2)
    s = d.size()
    ratio = (s.width() * s.height()) / base_area
    # Linear dims scale by sqrt(1.2); integer rounding keeps area within ~3%.
    assert ratio == pytest.approx(1.2, abs=0.05)


def test_sets_minimum_size_so_height_sticks(qapp):
    # QInputDialog pins its height unless a minimum is set — verify both grew.
    d = _input_dialog()
    base = d.sizeHint()
    _grow_dialog_area(d, 1.2)
    assert d.minimumSize().width() >= base.width()
    assert d.minimumSize().height() > base.height()


def test_factor_below_one_never_shrinks(qapp):
    d = _input_dialog()
    base = d.sizeHint()
    _grow_dialog_area(d, 0.5)  # clamped to 1.0 → no shrink
    assert d.size().width() >= base.width()
    assert d.size().height() >= base.height()
