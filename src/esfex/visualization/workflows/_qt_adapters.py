"""QThread adapters for standalone analysis packages.

Bridges the callback-based standalone libraries (windrex, solarex) to
PySide6 QThread + Signal patterns expected by the GUI wizard steps.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QThread, Signal


class QtWindAnalyzer(QThread):
    """QThread wrapper around windrex.WindAnalyzer."""

    progress = Signal(int, str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        bounds,
        wind_config,
        mcda_config,
        transmission_lines=None,
        parent=None,
    ):
        super().__init__(parent)
        self._bounds = bounds
        self._wind_config = wind_config
        self._mcda_config = mcda_config
        self._transmission_lines = transmission_lines
        self._analyzer = None

    def run(self):
        try:
            from windrex import WindAnalyzer

            config = replace(
                self._wind_config,
                bounds=self._bounds,
                mcda=self._mcda_config,
            )
            self._analyzer = WindAnalyzer(
                config, transmission_lines=self._transmission_lines,
            )
            result = self._analyzer.run(
                on_progress=lambda p, m: self.progress.emit(p, m),
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        if self._analyzer is not None:
            self._analyzer.cancel()


class QtSolarPVAnalyzer(QThread):
    """QThread wrapper around solarex.SolarPVAnalyzer."""

    progress = Signal(int, str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        bounds,
        solar_config,
        mcda_config,
        transmission_lines=None,
        parent=None,
    ):
        super().__init__(parent)
        self._bounds = bounds
        self._solar_config = solar_config
        self._mcda_config = mcda_config
        self._transmission_lines = transmission_lines
        self._analyzer = None

    def run(self):
        try:
            from solarex import SolarPVAnalyzer

            self._analyzer = SolarPVAnalyzer(
                self._bounds,
                self._solar_config,
                self._mcda_config,
                self._transmission_lines,
            )
            result = self._analyzer.run(
                on_progress=lambda p, m: self.progress.emit(p, m),
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        if self._analyzer is not None:
            self._analyzer.cancel()
