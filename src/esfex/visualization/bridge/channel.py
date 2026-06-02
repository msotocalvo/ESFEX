"""QWebChannel setup for Python <-> JavaScript communication."""

from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from esfex.visualization.bridge.js_bridge import MapBridge
from esfex.visualization.bridge.sld_bridge import SldBridge


def setup_channel(web_view: QWebEngineView) -> MapBridge:
    """Register a :class:`MapBridge` instance on *web_view*'s page.

    Returns the bridge object so callers can connect to its signals.
    """
    bridge = MapBridge()
    channel = QWebChannel(web_view.page())
    channel.registerObject("bridge", bridge)
    web_view.page().setWebChannel(channel)
    return bridge


def setup_sld_channel(web_view: QWebEngineView) -> SldBridge:
    """Register a :class:`SldBridge` instance on *web_view*'s page.

    Returns the bridge object so callers can connect to its signals.
    """
    bridge = SldBridge()
    channel = QWebChannel(web_view.page())
    channel.registerObject("sldBridge", bridge)
    web_view.page().setWebChannel(channel)
    return bridge
