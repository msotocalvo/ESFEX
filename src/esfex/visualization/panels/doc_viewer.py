"""Built-in documentation viewer with tree navigation, markdown rendering, and LaTeX math."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from esfex.visualization.i18n import tr

_DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"

# Display names for documentation sections (ordered).
_SECTIONS: list[tuple[str, str]] = [
    ("getting-started", "doc_viewer.sec_getting_started"),
    ("tutorials", "doc_viewer.sec_tutorials"),
    ("user-guide", "doc_viewer.sec_user_guide"),
    ("gui", "doc_viewer.sec_gui"),
    ("workflows", "doc_viewer.sec_workflows"),
    ("formulation", "doc_viewer.sec_formulation"),
    ("reference", "doc_viewer.sec_reference"),
    ("api", "doc_viewer.sec_api"),
    ("contributing", "doc_viewer.sec_contributing"),
]

# ---------------------------------------------------------------------------
# CSS for the rendered documentation
# ---------------------------------------------------------------------------

_DOC_CSS = """\
* { box-sizing: border-box; }
body {
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.75;
    color: #24292e;
    max-width: 900px;
    margin: 0 auto;
    padding: 24px 32px;
}
h1 {
    font-size: 16pt; font-weight: 700;
    margin: 48px 0 24px 0;
    padding-bottom: 8px;
    border-bottom: 2px solid #e0e0e0;
}
h2 {
    font-size: 14pt; font-weight: 600;
    margin: 40px 0 18px 0;
}
h3 {
    font-size: 12pt; font-weight: 600;
    margin: 32px 0 14px 0;
}
h4, h5, h6 {
    font-size: 11pt; font-weight: 600;
    margin: 24px 0 10px 0;
}
p { margin: 8px 0; }
a { color: #0366d6; text-decoration: none; }
a:hover { text-decoration: underline; }

/* Code */
code {
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
    font-size: 9pt;
    background: #f4f4f4;
    padding: 1px 5px;
    border-radius: 3px;
}
pre {
    background: #f6f8fa;
    border: 1px solid #e1e4e8;
    border-radius: 4px;
    padding: 12px 16px;
    margin: 12px 0;
    overflow-x: auto;
    line-height: 1.5;
}
pre code {
    background: none;
    padding: 0;
    border-radius: 0;
}

/* Tables */
table {
    border-collapse: collapse;
    width: 100%;
    margin: 16px 0;
    font-size: 10pt;
}
th {
    background: #f5f5f5;
    font-weight: 600;
    text-align: left;
    padding: 8px 12px;
    border: 1px solid #d0d0d0;
}
td {
    padding: 8px 12px;
    border: 1px solid #e0e0e0;
    vertical-align: top;
}
tr:nth-child(even) td {
    background: #fafafa;
}

/* Lists */
ul, ol { margin: 8px 0 8px 24px; }
li { margin: 4px 0; }

/* Blockquote (admonitions) */
blockquote {
    border-left: 4px solid #0366d6;
    margin: 12px 0;
    padding: 8px 16px;
    background: #f0f7ff;
    color: #24292e;
}

/* Horizontal rule */
hr {
    border: none;
    border-top: 1px solid #e0e0e0;
    margin: 24px 0;
}

/* MathJax display equations */
.MathJax_Display, mjx-container[display="true"] {
    overflow-x: auto;
    overflow-y: hidden;
    margin: 16px 0 !important;
}
"""

# ---------------------------------------------------------------------------
# MathJax configuration (matches docs/javascripts/mathjax.js)
# ---------------------------------------------------------------------------

_MATHJAX_CONFIG = """\
window.MathJax = {
  tex: {
    inlineMath: [["\\\\(", "\\\\)"]],
    displayMath: [["\\\\[", "\\\\]"]],
    processEscapes: true,
    processEnvironments: true
  }
};
"""

_MATHJAX_CDN = "https://unpkg.com/mathjax@3/es5/tex-mml-chtml.js"


def _pretty_name(filename: str) -> str:
    """Turn ``'config-reference.md'`` into ``'Config Reference'``."""
    stem = Path(filename).stem
    return stem.replace("-", " ").replace("_", " ").title()


def _title_for_path(path: Path) -> str:
    """Prefer the markdown H1 over the filename-derived name.

    Authors rename pages by changing the H1, not the filename — so a
    file called ``tutorials/mga.md`` whose H1 reads
    ``# Near-Optimal Alternatives`` should show up in the docs tree as
    that, not as ``Mga``. We read just the first non-blank line of the
    file and strip the leading ``#``s; if no H1 is found (or the file
    can't be read) we fall back to the filename-derived label."""
    try:
        with path.open(encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    # Strip leading "# ", trailing comment markers, and
                    # any trailing "{...}" attribute lists pyMdown supports.
                    title = line.lstrip("#").strip()
                    title = title.split("{", 1)[0].strip()
                    if title:
                        return title
                # First non-blank non-header line → no H1; bail.
                break
    except OSError:
        pass
    return _pretty_name(path.name)


def _md_to_html(text: str) -> str:
    """Convert markdown text to HTML, preserving LaTeX delimiters."""
    import markdown

    # python-markdown would mangle LaTeX backslashes inside paragraphs.
    # Protect \( \) \[ \] blocks before conversion, then restore them.
    import re

    placeholders: list[tuple[str, str]] = []

    def _protect(m: re.Match) -> str:
        key = f"MATHPLACEHOLDER{len(placeholders)}END"
        placeholders.append((key, m.group(0)))
        return key

    # Protect display math first (greedy), then inline math.
    protected = re.sub(r"\\\[.*?\\\]", _protect, text, flags=re.DOTALL)
    protected = re.sub(r"\\\(.*?\\\)", _protect, protected, flags=re.DOTALL)
    # Also protect $$...$$ (some docs use it).
    protected = re.sub(r"\$\$.*?\$\$", _protect, protected, flags=re.DOTALL)

    html = markdown.markdown(
        protected,
        extensions=["tables", "fenced_code", "toc", "attr_list", "def_list"],
    )

    # Restore LaTeX blocks.
    for key, original in placeholders:
        html = html.replace(key, original)

    return html


def _build_full_html(body_html: str) -> str:
    """Wrap converted HTML in a full document with MathJax and CSS."""
    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset='utf-8'>\n"
        f"<style>{_DOC_CSS}</style>\n"
        f"<script>{_MATHJAX_CONFIG}</script>\n"
        f"<script id='MathJax-script' async src='{_MATHJAX_CDN}'></script>\n"
        "</head><body>\n"
        f"{body_html}\n"
        "<script>\n"
        "document.getElementById('MathJax-script').addEventListener('load', function() {\n"
        "  if (window.MathJax && MathJax.typesetPromise) {\n"
        "    MathJax.typesetPromise();\n"
        "  }\n"
        "});\n"
        "</script>\n"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Custom QWebEnginePage to intercept link clicks
# ---------------------------------------------------------------------------


class _DocPage(QWebEnginePage):
    """Intercepts navigation to handle internal .md links and external URLs."""

    md_link_clicked = Signal(str, str)  # emits (resolved .md file path, fragment)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_dir: Path = _DOCS_DIR

    def set_base_dir(self, d: Path):
        self._base_dir = d

    def _deferred_emit(self, path: str, fragment: str):
        """Emit md_link_clicked outside the navigation callback."""
        self.md_link_clicked.emit(path, fragment)

    def _deferred_scroll(self, fragment: str):
        """Scroll to an anchor outside the navigation callback."""
        self.runJavaScript(
            f"document.getElementById('{fragment}')"
            f"?.scrollIntoView({{behavior:'smooth',block:'start'}});"
        )

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame):
        # Allow the initial page load and JS-driven content updates.
        if nav_type != QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            return True

        scheme = url.scheme()

        # External links → system browser.
        if scheme in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return False

        # Extract fragment.
        fragment = url.fragment() or ""

        # For file:// URLs, extract the local path directly.
        if scheme == "file":
            local_path = url.toLocalFile()
            if local_path:
                candidate = Path(local_path).resolve()
                if candidate.exists() and candidate.suffix == ".md":
                    # Defer to avoid destroying page state mid-callback.
                    QTimer.singleShot(0, lambda: self._deferred_emit(
                        str(candidate), fragment))
                    return False
                # Same-page anchor (file URL points to current page).
                if not local_path.strip() and fragment:
                    QTimer.singleShot(0, lambda: self._deferred_scroll(
                        fragment))
                    return False
            # file:// but not .md — open externally.
            QDesktopServices.openUrl(url)
            return False

        # Non-file, non-http scheme: try as relative path.
        raw = url.toString(QUrl.FormattingOptions(QUrl.UrlFormattingOption(0x0)))
        # Strip fragment from raw path string.
        if "#" in raw:
            raw, fragment = raw.rsplit("#", 1)

        # Same-page anchor navigation (empty path, only fragment).
        if not raw and fragment:
            QTimer.singleShot(0, lambda: self._deferred_scroll(fragment))
            return False

        if not raw:
            return False

        # Resolve relative to the current document directory.
        candidate = (self._base_dir / raw).resolve()
        if not candidate.exists():
            candidate = (_DOCS_DIR / raw).resolve()

        if candidate.exists() and candidate.suffix == ".md":
            QTimer.singleShot(0, lambda: self._deferred_emit(
                str(candidate), fragment))
            return False

        # Fallback: open in system browser.
        QDesktopServices.openUrl(url)
        return False


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------


class DocViewerDialog(QDialog):
    """Modal dialog with a tree sidebar and a markdown content area."""

    def __init__(self, parent=None, *, initial_section: str | None = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )
        self.setWindowTitle(tr("doc_viewer.title"))
        self.resize(1000, 700)
        self.setMinimumSize(700, 450)

        # --- layout ---
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal, self)
        root_layout.addWidget(splitter)

        # -- left: tree --
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(220)
        self._tree.setMaximumWidth(320)
        splitter.addWidget(self._tree)

        # -- right: web view --
        self._page = _DocPage(self)
        self._browser = QWebEngineView(self)
        self._browser.setPage(self._page)

        # Allow loading MathJax from CDN.
        settings = self._page.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )

        splitter.addWidget(self._browser)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 760])

        # -- bottom: customization bar --
        self._font_scale = 1.0  # 0.5 .. 2.5
        self._bg_gray = 255     # 255 = white, 0 = black

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 4, 12, 4)

        # Font size slider: range 50..250 representing 0.5x..2.5x
        font_label = QLabel(tr("doc_viewer.font_size"))
        font_label.setFixedWidth(110)
        self._font_slider = QSlider(Qt.Horizontal)
        self._font_slider.setRange(50, 250)
        self._font_slider.setValue(100)
        self._font_slider.setTickInterval(25)
        self._font_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._font_slider.setFixedWidth(180)
        self._font_value_label = QLabel("1.0x")
        self._font_value_label.setFixedWidth(36)
        bar.addWidget(font_label)
        bar.addSpacing(4)
        bar.addWidget(self._font_slider)
        bar.addSpacing(6)
        bar.addWidget(self._font_value_label)

        bar.addSpacing(28)

        # Background slider: range 0..255 (0 = black, 255 = white)
        bg_label = QLabel(tr("doc_viewer.background"))
        bg_label.setFixedWidth(110)
        self._bg_slider = QSlider(Qt.Horizontal)
        self._bg_slider.setRange(0, 255)
        self._bg_slider.setValue(255)
        self._bg_slider.setTickInterval(32)
        self._bg_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._bg_slider.setFixedWidth(180)
        self._bg_preview = QLabel()
        self._bg_preview.setFixedSize(20, 20)
        self._bg_preview.setStyleSheet(
            "background-color: #ffffff; border: 1px solid #999;"
        )
        bar.addWidget(bg_label)
        bar.addSpacing(4)
        bar.addWidget(self._bg_slider)
        bar.addSpacing(6)
        bar.addWidget(self._bg_preview)

        bar.addStretch()
        root_layout.addLayout(bar)

        # Map tree items → file paths.
        self._item_paths: dict[int, Path] = {}
        self._pending_fragment: str = ""

        self._populate_tree()
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._page.md_link_clicked.connect(self._on_md_link)
        self._page.loadFinished.connect(self._on_page_loaded)
        self._font_slider.valueChanged.connect(self._on_font_changed)
        self._bg_slider.valueChanged.connect(self._on_bg_changed)

        # Track the directory of the currently displayed file.
        self._current_dir: Path = _DOCS_DIR

        # Navigate to the requested section (or show index).
        if initial_section is not None:
            self._navigate_to_section(initial_section)
        else:
            index_path = _DOCS_DIR / "index.md"
            if index_path.exists():
                self._load_file(index_path)

    # ------------------------------------------------------------------
    # Tree construction
    # ------------------------------------------------------------------

    def _populate_tree(self):
        """Build the tree from the docs directory."""
        index_path = _DOCS_DIR / "index.md"
        if index_path.exists():
            home = QTreeWidgetItem([tr("doc_viewer.home")])
            home.setFont(0, QFont("Segoe UI", 10, QFont.Bold))
            self._tree.addTopLevelItem(home)
            self._item_paths[id(home)] = index_path

        for dir_name, tr_key in _SECTIONS:
            section_dir = _DOCS_DIR / dir_name
            if not section_dir.is_dir():
                continue

            md_files = sorted(section_dir.glob("*.md"))
            if not md_files:
                continue

            section_item = QTreeWidgetItem([tr(tr_key)])
            section_item.setFont(0, QFont("Segoe UI", 10, QFont.Bold))
            self._tree.addTopLevelItem(section_item)

            section_index = section_dir / "index.md"
            if section_index.exists():
                self._item_paths[id(section_item)] = section_index

            for md in md_files:
                if md.name == "index.md":
                    continue
                child = QTreeWidgetItem([_title_for_path(md)])
                section_item.addChild(child)
                self._item_paths[id(child)] = md

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int):
        path = self._item_paths.get(id(item))
        if path is not None:
            self._load_file(path)

    def _on_md_link(self, file_path_str: str, fragment: str = ""):
        """Handle an internal .md link intercepted by _DocPage."""
        path = Path(file_path_str)
        if path.exists():
            self._load_file(path, fragment=fragment)
            self._select_tree_item(path)

    def _load_file(self, path: Path, fragment: str = ""):
        """Read a markdown file, convert to HTML, and display with MathJax."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            self._browser.setHtml(
                f"<html><body><p>Could not read: {path}</p></body></html>"
            )
            return

        self._current_dir = path.parent
        self._page.set_base_dir(path.parent)
        self._pending_fragment = fragment

        body_html = _md_to_html(text)
        full_html = _build_full_html(body_html)

        # Use the file's parent as base URL so relative resources resolve.
        base_url = QUrl.fromLocalFile(str(path.parent) + "/")
        self._browser.setHtml(full_html, base_url)

    def _on_page_loaded(self, ok: bool):
        """Re-apply appearance settings and scroll to fragment after load."""
        if not ok:
            return
        if self._font_scale != 1.0 or self._bg_gray != 255:
            self._apply_appearance()
        fragment = getattr(self, "_pending_fragment", "")
        if fragment:
            self._page.runJavaScript(
                f"document.getElementById('{fragment}')"
                f"?.scrollIntoView({{behavior:'smooth',block:'start'}});"
            )
            self._pending_fragment = ""

    def _navigate_to_section(self, section: str):
        """Open a specific section by directory name or relative path."""
        target = _DOCS_DIR / section
        if target.is_file():
            self._load_file(target)
        elif target.is_dir():
            idx = target / "index.md"
            if idx.exists():
                self._load_file(idx)
            else:
                mds = sorted(target.glob("*.md"))
                if mds:
                    self._load_file(mds[0])

        self._select_tree_item(target)

    def _select_tree_item(self, target: Path):
        """Find and select the tree item matching *target*."""
        for item_id, path in self._item_paths.items():
            if path == target or (target.is_dir() and path.parent == target):
                for i in range(self._tree.topLevelItemCount()):
                    top = self._tree.topLevelItem(i)
                    if id(top) == item_id:
                        self._tree.setCurrentItem(top)
                        top.setExpanded(True)
                        return
                    for j in range(top.childCount()):
                        child = top.child(j)
                        if id(child) == item_id:
                            top.setExpanded(True)
                            self._tree.setCurrentItem(child)
                            return
                break

    # ------------------------------------------------------------------
    # Appearance customization
    # ------------------------------------------------------------------

    def _on_font_changed(self, value: int):
        """Scale all fonts proportionally.  Slider range 50..250 → 0.5x..2.5x."""
        self._font_scale = value / 100.0
        self._font_value_label.setText(f"{self._font_scale:.1f}x")
        self._apply_appearance()

    def _on_bg_changed(self, value: int):
        """Change background on a grayscale ramp.  255 = white, 0 = black."""
        self._bg_gray = value
        hex_color = f"#{value:02x}{value:02x}{value:02x}"
        self._bg_preview.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid #999;"
        )
        self._apply_appearance()

    def _apply_appearance(self):
        """Inject JS to update font size and background color in the web view."""
        scale = self._font_scale
        g = self._bg_gray
        # Auto-select light or dark text depending on background brightness.
        text_color = "#e8e8e8" if g < 128 else "#24292e"
        link_color = "#6ab0f3" if g < 128 else "#0366d6"
        bg_hex = f"#{g:02x}{g:02x}{g:02x}"
        # Slightly offset shades for code blocks and table headers.
        code_g = min(g + 15, 255) if g < 128 else max(g - 10, 0)
        code_hex = f"#{code_g:02x}{code_g:02x}{code_g:02x}"
        th_g = min(g + 10, 255) if g < 128 else max(g - 6, 0)
        th_hex = f"#{th_g:02x}{th_g:02x}{th_g:02x}"
        stripe_g = min(g + 6, 255) if g < 128 else max(g - 3, 0)
        stripe_hex = f"#{stripe_g:02x}{stripe_g:02x}{stripe_g:02x}"
        border_color = "#555" if g < 128 else "#e0e0e0"
        bq_bg = f"#{min(g + 20, 255):02x}{min(g + 20, 255):02x}{min(g + 30, 255):02x}" if g < 128 else "#f0f7ff"

        js = f"""(function() {{
            var s = document.body.style;
            s.fontSize = ({scale} * 10) + 'pt';
            s.backgroundColor = '{bg_hex}';
            s.color = '{text_color}';
            // Scale headings proportionally
            var tags = {{'H1': {scale*16}, 'H2': {scale*14}, 'H3': {scale*12},
                        'H4': {scale*11}, 'H5': {scale*11}, 'H6': {scale*11}}};
            for (var tag in tags) {{
                var els = document.getElementsByTagName(tag);
                for (var i = 0; i < els.length; i++) {{
                    els[i].style.fontSize = tags[tag] + 'pt';
                }}
            }}
            // Links
            var links = document.getElementsByTagName('A');
            for (var i = 0; i < links.length; i++) links[i].style.color = '{link_color}';
            // Code blocks
            var codes = document.getElementsByTagName('CODE');
            for (var i = 0; i < codes.length; i++) {{
                codes[i].style.fontSize = ({scale} * 9) + 'pt';
                codes[i].style.backgroundColor = '{code_hex}';
            }}
            var pres = document.getElementsByTagName('PRE');
            for (var i = 0; i < pres.length; i++) {{
                pres[i].style.backgroundColor = '{code_hex}';
                pres[i].style.borderColor = '{border_color}';
            }}
            // Tables
            var ths = document.getElementsByTagName('TH');
            for (var i = 0; i < ths.length; i++) {{
                ths[i].style.backgroundColor = '{th_hex}';
                ths[i].style.borderColor = '{border_color}';
                ths[i].style.fontSize = ({scale} * 10) + 'pt';
            }}
            var tds = document.getElementsByTagName('TD');
            for (var i = 0; i < tds.length; i++) {{
                tds[i].style.borderColor = '{border_color}';
                tds[i].style.fontSize = ({scale} * 10) + 'pt';
            }}
            // Striped rows
            var trs = document.getElementsByTagName('TR');
            for (var i = 0; i < trs.length; i++) {{
                if (i % 2 === 1) {{
                    var cells = trs[i].getElementsByTagName('TD');
                    for (var j = 0; j < cells.length; j++)
                        cells[j].style.backgroundColor = '{stripe_hex}';
                }}
            }}
            // Blockquotes
            var bqs = document.getElementsByTagName('BLOCKQUOTE');
            for (var i = 0; i < bqs.length; i++) {{
                bqs[i].style.backgroundColor = '{bq_bg}';
                bqs[i].style.color = '{text_color}';
            }}
            // HR
            var hrs = document.getElementsByTagName('HR');
            for (var i = 0; i < hrs.length; i++)
                hrs[i].style.borderTopColor = '{border_color}';
        }})();"""
        self._page.runJavaScript(js)
