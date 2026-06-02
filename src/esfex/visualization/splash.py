"""Splash / loading screen shown during ESFEX Studio startup."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

_ICONS_DIR = Path(__file__).resolve().parents[1] / "icons"

# Hardcoded defaults matching ColorPalette — used before theme is applied
_DEFAULT_BG = "#FFFFFF"
_DEFAULT_TEXT = "#2C3E50"
_DEFAULT_TEXT_SEC = "#7F8C8D"
_DEFAULT_ACCENT = "#2980B9"
_DEFAULT_BORDER = "#DEE2E6"
_DEFAULT_SURFACE_SEC = "#F5F7FA"


class SplashScreen(QWidget):
    """Frameless loading screen shown while the editor initializes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(540, 312)
        # Match the app-wide window icon (some Linux compositors don't
        # propagate QApplication.windowIcon to splash windows).
        _icon_path = _ICONS_DIR / "icon.svg"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 28)
        # Tight spacing so the version label sits close to the logo's
        # bottom edge; vertical separation between (version) and
        # (status + progress) comes from the explicit `addStretch()` below.
        layout.setSpacing(2)

        # Logo: PNG scaled to fit a 440×200 box while preserving aspect ratio.
        # Source is 1123×794 (ratio 1.414); QPixmap.scaled with KeepAspectRatio
        # picks the tighter constraint, so the logo lands at ~283×200 px.
        self._logo_label = QLabel()
        self._logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_path = _ICONS_DIR / "esfex.png"
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path)).scaled(
                440, 200,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._logo_label.setPixmap(pixmap)
        layout.addWidget(self._logo_label)

        # Version label
        self._version_label = QLabel()
        self._version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            from esfex import __version__
            ver = __version__
        except Exception:
            ver = ""
        self._version_label.setText(f"v{ver}" if ver else "")
        layout.addWidget(self._version_label)

        # Fixed gap between the version label and the status/progress block,
        # replacing the previous addStretch() (which absorbed ~36 px). 7 px
        # is ~20% of that, the splash height was reduced by the difference
        # so nothing overflows.
        layout.addSpacing(7)

        # Status text
        self._status_label = QLabel("Starting...")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        # Progress bar
        layout.addSpacing(8)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        layout.addWidget(self._progress)

        # Apply default (pre-theme) styling
        self._apply_style(
            _DEFAULT_BG, _DEFAULT_TEXT, _DEFAULT_TEXT_SEC,
            _DEFAULT_ACCENT, _DEFAULT_BORDER, _DEFAULT_SURFACE_SEC,
        )

    # ------------------------------------------------------------------

    def _apply_style(self, bg, text, text_sec, accent, border, surface_sec):
        self.setStyleSheet(f"""
            SplashScreen {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
        """)
        self._version_label.setStyleSheet(f"color: {text_sec}; font-size: 11px;")
        self._status_label.setStyleSheet(f"color: {text}; font-size: 12px;")
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background: {surface_sec};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {accent};
                border-radius: 3px;
            }}
        """)

    def restyle(self):
        """Re-apply styling after the theme is loaded."""
        from esfex.visualization.theme import current_theme

        c = current_theme().colors
        self._apply_style(
            c.surface_primary, c.text_primary, c.text_secondary,
            c.accent_primary, c.border_light, c.surface_secondary,
        )
        QApplication.processEvents()

    def set_progress(self, value: int, message: str):
        """Update progress bar and status message, then pump events."""
        self._progress.setValue(value)
        self._status_label.setText(message)
        QApplication.processEvents()

    def show(self):
        """Show centered on primary screen."""
        super().show()
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)
        QApplication.processEvents()

    def finish(self, main_window: QWidget):
        """Close the splash and show the main window."""
        self.close()
        main_window.show()
        self.deleteLater()
