from __future__ import annotations

THEMES = {
    # Official-site inspired palettes: near-black/white surfaces, strong orange
    # identity accents, and cool cyan for secondary tactical information.
    "dark": {
        "label": "전술 블랙",
        "BUTTON_BASE": "#161C21",
        "BUTTON_BORDER": "#39434A",
        "NAV_ACTIVE": "#20272D",
        "BG": "#080B0E",
        "SIDEBAR": "#0B0F13",
        "PANEL": "#11171C",
        "PANEL_ALT": "#192127",
        "BORDER": "#303A41",
        "TEXT": "#F3F5F4",
        "MUTED": "#929DA3",
        "ACCENT": "#F26C1C",
        "ACCENT_HOVER": "#FF8135",
        "SUCCESS": "#67C997",
        "DANGER": "#EC6C6C",
        "DANGER_TEXT": "#FFB9B9",
        "INFO": "#49BAC2",
        "TERRAIN_BLOCK": "#030405",
        "COVER": "#A0AAAF",
        "BUTTON_HOVER": "#222C32",
        "BUTTON_PRESSED": "#2A353C",
        "DISABLED_BG": "#101519",
        "DISABLED_TEXT": "#59646A",
        "DISABLED_BORDER": "#252D32",
        "SELECT": "#26383B",
        "ACCENT_TEXT": "#130B06",
        "DANGER_BG": "#351E21",
        "DANGER_BORDER": "#63353A",
        "DANGER_HOVER": "#48262A",
        "SCROLL": "#38454B",
        "SCROLL_HOVER": "#4C5C63",
        "LOG_BG": "#090D10",
        "TOOLTIP_BG": "#11171B",
    },
    "light": {
        "label": "클린 화이트",
        "BUTTON_BASE": "#F4F2ED",
        "BUTTON_BORDER": "#C9C6BE",
        "NAV_ACTIVE": "#FCE9DD",
        "BG": "#F2F2EF",
        "SIDEBAR": "#FFFFFF",
        "PANEL": "#FFFFFF",
        "PANEL_ALT": "#F5F5F2",
        "BORDER": "#D2D2CC",
        "TEXT": "#181B1E",
        "MUTED": "#697177",
        "ACCENT": "#E85F14",
        "ACCENT_HOVER": "#F57429",
        "SUCCESS": "#27855B",
        "DANGER": "#C84747",
        "DANGER_TEXT": "#9C3030",
        "INFO": "#25858F",
        "TERRAIN_BLOCK": "#292D30",
        "COVER": "#666E73",
        "BUTTON_HOVER": "#ECEAE5",
        "BUTTON_PRESSED": "#E2DFD8",
        "DISABLED_BG": "#ECECE8",
        "DISABLED_TEXT": "#9CA2A6",
        "DISABLED_BORDER": "#DCDCD7",
        "SELECT": "#DCECEE",
        "ACCENT_TEXT": "#FFFFFF",
        "DANGER_BG": "#FAEAEA",
        "DANGER_BORDER": "#E2B9B9",
        "DANGER_HOVER": "#F4DCDC",
        "SCROLL": "#BBC0C2",
        "SCROLL_HOVER": "#9BA3A6",
        "LOG_BG": "#FAFAF8",
        "TOOLTIP_BG": "#FFFFFF",
    },
    "midnight": {
        "label": "엘모 네이비",
        "BUTTON_BASE": "#102B38",
        "BUTTON_BORDER": "#315868",
        "NAV_ACTIVE": "#173A48",
        "BG": "#061118",
        "SIDEBAR": "#081720",
        "PANEL": "#0D202A",
        "PANEL_ALT": "#132B36",
        "BORDER": "#294652",
        "TEXT": "#EFF6F6",
        "MUTED": "#8CA3AA",
        "ACCENT": "#F26C1C",
        "ACCENT_HOVER": "#FF8237",
        "SUCCESS": "#5FD09B",
        "DANGER": "#F17272",
        "DANGER_TEXT": "#FFBBBB",
        "INFO": "#50C3CF",
        "TERRAIN_BLOCK": "#02070A",
        "COVER": "#A1B7BD",
        "BUTTON_HOVER": "#173744",
        "BUTTON_PRESSED": "#1E4352",
        "DISABLED_BG": "#0B1921",
        "DISABLED_TEXT": "#557079",
        "DISABLED_BORDER": "#1E333D",
        "SELECT": "#1D4650",
        "ACCENT_TEXT": "#140B05",
        "DANGER_BG": "#3A2025",
        "DANGER_BORDER": "#69363E",
        "DANGER_HOVER": "#4D2930",
        "SCROLL": "#315664",
        "SCROLL_HOVER": "#44717F",
        "LOG_BG": "#07131A",
        "TOOLTIP_BG": "#0B1B23",
    },
    "graphite": {
        "label": "그리폰 그레이",
        "BUTTON_BASE": "#2A2D30",
        "BUTTON_BORDER": "#50565B",
        "NAV_ACTIVE": "#353A3E",
        "BG": "#16181A",
        "SIDEBAR": "#1B1E20",
        "PANEL": "#222527",
        "PANEL_ALT": "#2A2E31",
        "BORDER": "#41474B",
        "TEXT": "#F3F3F1",
        "MUTED": "#A2A6A7",
        "ACCENT": "#F26C1C",
        "ACCENT_HOVER": "#FF8237",
        "SUCCESS": "#70CA9A",
        "DANGER": "#EE7474",
        "DANGER_TEXT": "#FFC0C0",
        "INFO": "#71B8C1",
        "TERRAIN_BLOCK": "#090A0B",
        "COVER": "#B2B6B8",
        "BUTTON_HOVER": "#34383B",
        "BUTTON_PRESSED": "#3D4245",
        "DISABLED_BG": "#202224",
        "DISABLED_TEXT": "#6F7476",
        "DISABLED_BORDER": "#303437",
        "SELECT": "#3A4446",
        "ACCENT_TEXT": "#160B05",
        "DANGER_BG": "#442629",
        "DANGER_BORDER": "#704045",
        "DANGER_HOVER": "#573034",
        "SCROLL": "#50575B",
        "SCROLL_HOVER": "#666E73",
        "LOG_BG": "#131517",
        "TOOLTIP_BG": "#202326",
    },
}

_ACTIVE_THEME = "dark"

# Keep the palette names as real module symbols. Besides helping static quality
# checks, this makes custom-painted widgets able to reference theme.<name> while
# set_active_theme() updates their values in place.
BG = str(THEMES["dark"]["BG"])
SIDEBAR = str(THEMES["dark"]["SIDEBAR"])
PANEL = str(THEMES["dark"]["PANEL"])
PANEL_ALT = str(THEMES["dark"]["PANEL_ALT"])
BORDER = str(THEMES["dark"]["BORDER"])
TEXT = str(THEMES["dark"]["TEXT"])
MUTED = str(THEMES["dark"]["MUTED"])
ACCENT = str(THEMES["dark"]["ACCENT"])
ACCENT_HOVER = str(THEMES["dark"]["ACCENT_HOVER"])
SUCCESS = str(THEMES["dark"]["SUCCESS"])
DANGER = str(THEMES["dark"]["DANGER"])
DANGER_TEXT = str(THEMES["dark"]["DANGER_TEXT"])
INFO = str(THEMES["dark"]["INFO"])
TERRAIN_BLOCK = str(THEMES["dark"]["TERRAIN_BLOCK"])
COVER = str(THEMES["dark"]["COVER"])
BUTTON_BASE = str(THEMES["dark"]["BUTTON_BASE"])
BUTTON_BORDER = str(THEMES["dark"]["BUTTON_BORDER"])
NAV_ACTIVE = str(THEMES["dark"]["NAV_ACTIVE"])
BUTTON_HOVER = str(THEMES["dark"]["BUTTON_HOVER"])
BUTTON_PRESSED = str(THEMES["dark"]["BUTTON_PRESSED"])
DISABLED_BG = str(THEMES["dark"]["DISABLED_BG"])
DISABLED_TEXT = str(THEMES["dark"]["DISABLED_TEXT"])
DISABLED_BORDER = str(THEMES["dark"]["DISABLED_BORDER"])
SELECT = str(THEMES["dark"]["SELECT"])
ACCENT_TEXT = str(THEMES["dark"]["ACCENT_TEXT"])
DANGER_BG = str(THEMES["dark"]["DANGER_BG"])
DANGER_BORDER = str(THEMES["dark"]["DANGER_BORDER"])
DANGER_HOVER = str(THEMES["dark"]["DANGER_HOVER"])
SCROLL = str(THEMES["dark"]["SCROLL"])
SCROLL_HOVER = str(THEMES["dark"]["SCROLL_HOVER"])
LOG_BG = str(THEMES["dark"]["LOG_BG"])
TOOLTIP_BG = str(THEMES["dark"]["TOOLTIP_BG"])


def set_active_theme(name: str) -> str:
    global _ACTIVE_THEME
    key = str(name or "dark")
    if key not in THEMES:
        key = "dark"
    _ACTIVE_THEME = key
    globals().update({k: v for k, v in THEMES[key].items() if k != "label"})
    panels = globals().get("FACTOR_PANEL_THEMES", {}).get(key)
    current_panels = globals().get("FACTOR_PANEL_COLORS")
    if panels is not None and isinstance(current_panels, dict):
        current_panels.clear()
        current_panels.update(panels)
    return key


def active_theme() -> str:
    return _ACTIVE_THEME


def theme_choices() -> list[tuple[str, str]]:
    return [(key, str(value["label"])) for key, value in THEMES.items()]


FACTOR_ORDER = ["sentinel", "vanguard", "bulwark", "support"]
FACTOR_COLORS = {
    "sentinel": "#E45757",
    "support": "#58D68D",
    "bulwark": "#70A5FF",
    "vanguard": "#B58AF2",
}
FACTOR_PANEL_THEMES = {
    "dark": {
        "sentinel": "#351D20",
        "support": "#173128",
        "bulwark": "#182A3C",
        "vanguard": "#2A213B",
    },
    "light": {
        "sentinel": "#FFF0F0",
        "support": "#E9F8F0",
        "bulwark": "#EAF3FC",
        "vanguard": "#F4EEFC",
    },
    "midnight": {
        "sentinel": "#352329",
        "support": "#14342D",
        "bulwark": "#163342",
        "vanguard": "#282B43",
    },
    "graphite": {
        "sentinel": "#3A2528",
        "support": "#22352D",
        "bulwark": "#263341",
        "vanguard": "#342D43",
    },
}
FACTOR_PANEL_COLORS = dict(FACTOR_PANEL_THEMES["dark"])
ELEMENT_ORDER = ["physical", "burn", "electric", "freeze", "corrosion", "hydro", "omni"]
ELEMENT_COLORS = {
    "physical": "#A7B0BA",
    "burn": "#FF6B57",
    "electric": "#F3C744",
    "freeze": "#78D7F0",
    "corrosion": "#A56CE6",
    "hydro": "#5BB7E8",
    "omni": "#8F3655",
}

set_active_theme("dark")


def qt_palette():
    """Build a full Qt palette so native widgets follow the selected theme too."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: PANEL,
        QPalette.ColorRole.AlternateBase: PANEL_ALT,
        QPalette.ColorRole.ToolTipBase: TOOLTIP_BG,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: PANEL_ALT,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.BrightText: DANGER_TEXT,
        QPalette.ColorRole.Highlight: SELECT,
        QPalette.ColorRole.HighlightedText: TEXT,
        QPalette.ColorRole.Link: INFO,
        QPalette.ColorRole.LinkVisited: ACCENT,
        QPalette.ColorRole.PlaceholderText: MUTED,
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(DISABLED_TEXT))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(DISABLED_TEXT))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(DISABLED_BG))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(DISABLED_BG))
    return palette


def _fast_tooltip_style():
    """Return Fusion wrapped with a short, consistent tooltip wake-up delay."""
    from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleFactory

    class FastToolTipStyle(QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802
            if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
                return 160
            return super().styleHint(hint, option, widget, returnData)

    return FastToolTipStyle(QStyleFactory.create("Fusion"))


def apply_to_application(app) -> None:
    """Apply both native palette and QSS; avoid window-local stale stylesheets."""
    app.setStyle(_fast_tooltip_style())
    app.setPalette(qt_palette())
    # Replacing the application QSS forces all existing widgets to repolish.
    app.setStyleSheet("")
    app.setStyleSheet(stylesheet())


def _rgba(hex_color: str, alpha: int) -> str:
    value = str(hex_color).lstrip("#")
    if len(value) != 6:
        return f"rgba(0,0,0,{max(0, min(255, int(alpha)))})"
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"rgba({r},{g},{b},{max(0, min(255, int(alpha)))})"


# Stable light palette used only for share/export images. Keeping these in the
# theme module preserves the project's single color contract while exports stay
# readable regardless of the active application theme.
def stylesheet() -> str:
    return f"""
    * {{
        font-family: 'Segoe UI', 'Malgun Gothic', sans-serif;
        font-size: 10pt;
    }}
    QMainWindow, QWidget#AppRoot {{
        background: {BG};
        color: {TEXT};
    }}
    QWidget {{
        color: {TEXT};
    }}
    QMenuBar, QMenu {{
        background: {PANEL};
        color: {TEXT};
        border-color: {BORDER};
    }}
    QMenu::item:selected {{
        background: {SELECT};
    }}
    QFrame#Sidebar {{
        background: {SIDEBAR};
        border: 0;
        border-right: 1px solid {BORDER};
    }}
    QFrame#SidebarSeparator {{
        border: 0;
        border-top: 1px solid {BORDER};
        margin: 6px 2px;
    }}
    QFrame#Panel, QGroupBox {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-left: 2px solid {_rgba(ACCENT, 150)};
        border-radius: 4px;
    }}
    QFrame#PanelAlt {{
        background: {PANEL_ALT};
        border: 1px solid {BORDER};
        border-radius: 4px;
    }}
    QGroupBox {{
        margin-top: 10px;
        padding-top: 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {TEXT};
    }}
    QLabel#PageTitle {{
        font-size: 18pt;
        font-weight: 750;
        border-left: 3px solid {ACCENT};
        padding-left: 10px;
    }}
    QLabel#SectionTitle {{
        font-size: 11pt;
        font-weight: 650;
    }}
    QLabel#Muted {{
        color: {MUTED};
    }}
    QLabel#Metric {{
        font-size: 20pt;
        font-weight: 700;
        color: {TEXT};
    }}
    QLabel#BrandTitle {{
        color: {TEXT};
        font-size: 17pt;
        font-weight: 850;
        letter-spacing: 0.6px;
    }}
    QLabel#BrandSubtitle {{
        color: {ACCENT};
        font-size: 8pt;
        font-weight: 650;
    }}
    QPushButton {{
        background: {BUTTON_BASE};
        color: {TEXT};
        border: 1px solid {BUTTON_BORDER};
        border-radius: 4px;
        padding: 7px 12px;
    }}
    QPushButton:hover {{
        background: {BUTTON_HOVER};
    }}
    QPushButton:pressed {{
        background: {BUTTON_PRESSED};
    }}
    QPushButton:disabled {{
        background: {DISABLED_BG};
        color: {DISABLED_TEXT};
        border-color: {DISABLED_BORDER};
    }}
    QPushButton#AccentButton {{
        background: {ACCENT};
        color: {ACCENT_TEXT};
        border: 0;
        font-weight: 700;
    }}
    QPushButton#AccentButton:hover {{
        background: {ACCENT_HOVER};
    }}
    QPushButton#DangerButton {{
        background: {DANGER_BG};
        color: {DANGER_TEXT};
        border-color: {DANGER_BORDER};
    }}
    QPushButton#DangerButton:hover {{
        background: {DANGER_HOVER};
        border-color: {DANGER};
    }}
    QToolButton#StepButton {{
        background: {SCROLL};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 4px;
        min-width: 24px;
        max-width: 24px;
        min-height: 24px;
        font-size: 12pt;
        font-weight: 800;
        padding: 0;
    }}
    QToolButton#StepButton:hover {{
        background: {SCROLL_HOVER};
        border-color: {ACCENT};
    }}
    QToolButton#HelpIcon, QToolButton#WarningHelpIcon {{
        background: {PANEL_ALT};
        border: 1px solid {ACCENT};
        border-radius: 12px;
        color: {ACCENT};
        font-size: 10pt;
        font-weight: 800;
        padding: 0;
    }}
    QToolButton#HelpIcon:hover {{
        color: {ACCENT};
        border-color: {ACCENT};
        background: {BUTTON_HOVER};
    }}
    QToolButton#WarningHelpIcon {{
        color: {DANGER_TEXT};
        border-color: {DANGER_BORDER};
        background: {DANGER_BG};
    }}
    QToolButton#WarningHelpIcon:hover {{
        color: {DANGER};
        border-color: {DANGER};
        background: {DANGER_BG};
    }}
    QPushButton#NavButton {{
        border: 0;
        border-radius: 4px;
        text-align: left;
        padding: 10px 11px;
        color: {MUTED};
        background: transparent;
    }}
    QPushButton#NavButton:hover {{
        background: {BUTTON_HOVER};
        color: {TEXT};
    }}
    QPushButton#NavButton:checked {{
        background: {NAV_ACTIVE};
        color: {TEXT};
        border-left: 3px solid {ACCENT};
        font-weight: 700;
    }}
    QPushButton#DictionaryCategoryButton {{
        min-width: 96px;
        padding: 8px 14px;
        background: {PANEL};
        color: {MUTED};
        border-color: {BORDER};
    }}
    QPushButton#DictionaryCategoryButton:hover {{
        color: {TEXT};
        border-color: {INFO};
    }}
    QPushButton#DictionaryCategoryButton:checked {{
        color: {TEXT};
        background: {NAV_ACTIVE};
        border-color: {ACCENT};
        border-bottom: 2px solid {ACCENT};
        font-weight: 700;
    }}
    QToolButton#DictionaryDollCard {{
        background: {PANEL_ALT};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 3px;
        padding: 7px;
        font-weight: 650;
    }}
    QToolButton#DictionaryDollCard:hover {{
        background: {BUTTON_HOVER};
        border-color: {INFO};
    }}
    QLabel#DictionaryPortrait {{
        background: {PANEL_ALT};
        color: {MUTED};
        border: 1px solid {BORDER};
        border-radius: 3px;
        font-size: 28pt;
    }}
    QLabel#DictionaryDollTitle {{
        font-size: 22pt;
        font-weight: 850;
    }}
    QFrame#DictionaryHero {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-top: 3px solid {ACCENT};
        border-radius: 4px;
    }}
    QLabel#DictionaryIntro {{
        color: {TEXT};
        font-size: 11pt;
        line-height: 1.5;
    }}
    QToolButton#DictionaryArtButton {{
        background: {PANEL_ALT};
        color: {MUTED};
        border: 1px solid {BORDER};
        border-radius: 3px;
        padding: 6px;
        font-size: 9pt;
        font-weight: 650;
    }}
    QToolButton#DictionaryArtButton:hover {{
        border-color: {ACCENT};
        background: {BUTTON_HOVER};
        color: {TEXT};
    }}
    QComboBox#DictionaryFilterCombo {{
        min-height: 24px;
        padding: 6px 30px 6px 10px;
        background: {PANEL};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-bottom: 2px solid {INFO};
        border-radius: 3px;
        font-weight: 650;
    }}
    QComboBox#DictionaryFilterCombo:hover {{
        border-color: {INFO};
        border-bottom-color: {ACCENT};
    }}
    QPushButton#DictionaryFilterReset {{
        padding: 7px 11px;
        background: transparent;
        color: {MUTED};
        border: 1px solid {BORDER};
        border-radius: 3px;
    }}
    QPushButton#DictionaryFilterReset:hover {{
        color: {TEXT};
        background: {BUTTON_HOVER};
        border-color: {INFO};
    }}
    QFrame#DictionaryNavigatorPane, QFrame#DictionaryReadingPane {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 4px;
    }}
    QFrame#DictionaryNavigatorPane {{
        border-top: 3px solid {INFO};
    }}
    QFrame#DictionaryReadingPane {{
        border-top: 3px solid {ACCENT};
    }}
    QLabel#DictionaryPaneTitle {{
        color: {TEXT};
        font-size: 11.5pt;
        font-weight: 800;
        padding: 1px 2px;
    }}
    QLabel#DictionaryPaneCount {{
        color: {MUTED};
        font-size: 9pt;
        padding: 1px 2px;
    }}
    QLabel#DictionaryStickyHeader {{
        color: {TEXT};
        background: {NAV_ACTIVE};
        border: 1px solid {BORDER};
        border-left: 4px solid {ACCENT};
        border-radius: 3px;
        padding: 7px 10px;
        font-size: 10pt;
        font-weight: 750;
    }}
    QListWidget#DictionaryEntryList {{
        background: {PANEL_ALT};
        border: 1px solid {BORDER};
        border-radius: 3px;
        padding: 5px;
        font-size: 10.5pt;
        outline: none;
    }}
    QListWidget#DictionaryEntryList::item {{
        border: 0;
        border-bottom: 1px solid {_rgba(BORDER, 150)};
        padding: 9px 10px;
        margin: 1px 0;
    }}
    QListWidget#DictionaryEntryList::item:hover {{
        background: {BUTTON_HOVER};
        color: {TEXT};
    }}
    QListWidget#DictionaryEntryList::item:selected {{
        background: {SELECT};
        color: {TEXT};
        border-left: 4px solid {ACCENT};
        font-weight: 700;
    }}
    QTextBrowser#DictionaryReader {{
        background: {PANEL_ALT};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 3px;
        padding: 0;
        font-size: 12.5pt;
        selection-background-color: {SELECT};
    }}
    QSplitter#DictionaryReaderSplitter::handle {{
        background: {BG};
        width: 8px;
    }}
    QTabWidget::pane {{
        border: 1px solid {BORDER};
        background: {PANEL};
        top: -1px;
    }}
    QTabBar::tab {{
        background: {PANEL_ALT};
        color: {MUTED};
        border: 1px solid {BORDER};
        padding: 8px 14px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        color: {TEXT};
        border-bottom: 2px solid {ACCENT};
        background: {NAV_ACTIVE};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
        background: {PANEL_ALT};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 3px;
        padding: 6px 8px;
        min-height: 20px;
        selection-background-color: {SELECT};
        selection-color: {TEXT};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QTextEdit:focus, QPlainTextEdit:focus, QTableView:focus, QListView:focus, QTreeView:focus {{
        border-color: {INFO};
    }}
    QComboBox QAbstractItemView {{
        background: {PANEL_ALT};
        color: {TEXT};
        selection-background-color: {ACCENT};
        selection-color: {ACCENT_TEXT};
    }}
    QCheckBox {{
        spacing: 6px;
    }}
    QTableView, QTreeView, QTreeWidget, QListView {{
        background: {PANEL};
        alternate-background-color: {PANEL_ALT};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 7px;
        outline: 0;
        selection-background-color: {SELECT};
        selection-color: {TEXT};
    }}
    QScrollArea#ResultScroll, QScrollArea#GroupedDollScroll,
    QScrollArea#FormationMemberScroll, QScrollArea#TargetProfileScroll,
    QScrollArea#DictionaryDollScroll {{
        background: {BG};
        border: 0;
    }}
    QTreeWidget#RemoldingGroupedTree {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 7px;
        padding: 2px;
    }}
    QTextEdit#LogView, QPlainTextEdit#LogView {{
        background: {LOG_BG};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 8px;
    }}
    QWidget#ResultBody, QWidget#GroupedDollBody, QWidget#FormationMemberBody,
    QWidget#DictionaryDollBody {{
        background: {BG};
    }}
    QHeaderView::section {{
        background: {PANEL_ALT};
        color: {MUTED};
        border: 0;
        border-bottom: 1px solid {BORDER};
        padding: 7px;
        font-weight: 650;
    }}
    QScrollBar:vertical {{
        background: {PANEL};
        width: 11px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {SCROLL};
        min-height: 28px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {SCROLL_HOVER};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: {PANEL};
        height: 11px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {SCROLL};
        min-width: 28px;
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {SCROLL_HOVER};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QSplitter::handle {{
        background: {BG};
    }}
    QToolTip {{
        background: {TOOLTIP_BG};
        color: {TEXT};
        border: 1px solid {BORDER};
        padding: 5px;
    }}
    QDialog {{
        background: {BG};
        color: {TEXT};
    }}
    QMessageBox {{
        background: {BG};
    }}
    QTabWidget::pane {{
        border: 1px solid {BORDER};
        border-radius: 7px;
        background: {PANEL};
    }}
    QTabBar::tab {{
        background: {PANEL_ALT};
        color: {MUTED};
        padding: 8px 14px;
        border: 1px solid {BORDER};
        border-bottom: 0;
    }}
    QTabBar::tab:selected {{
        color: {ACCENT};
        background: {NAV_ACTIVE};
        border-top: 2px solid {ACCENT};
        font-weight: 700;
    }}
    QLabel#PortraitSurface {{
        background: {PANEL_ALT};
        border: 1px solid {BORDER};
        border-radius: 7px;
        color: {MUTED};
    }}
    QFrame#SelectedDollPanel {{
        background: {PANEL_ALT};
        border: 1px solid {ACCENT};
        border-radius: 9px;
    }}
    QLabel#SelectionChip {{
        background: {SELECT};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 9px;
        padding: 4px 8px;
        font-weight: 650;
    }}
    QLabel#WarningText {{
        color: {DANGER_TEXT};
    }}
    QLabel#AccentText {{
        color: {ACCENT};
        font-weight: 700;
    }}
    QLabel#SuccessText {{
        color: {SUCCESS};
        font-weight: 800;
    }}
    QLabel#DangerText {{
        color: {DANGER};
        font-weight: 800;
    }}
    QLabel#OverlayTitle {{
        color: {TEXT};
        background: {_rgba(PANEL, 220)};
        padding: 8px;
        border-radius: 5px;
        font-size: 18px;
        font-weight: 700;
    }}
    QLabel#OverlayCycle {{
        color: {TEXT};
        background: {_rgba(INFO, 190)};
        padding: 9px;
        border-radius: 5px;
        font-size: 15px;
        font-weight: 700;
    }}
    QLabel#OverlayNote {{
        color: {TEXT};
        background: {_rgba(PANEL, 220)};
        padding: 8px;
        border-radius: 5px;
        font-size: 14px;
    }}
    """
