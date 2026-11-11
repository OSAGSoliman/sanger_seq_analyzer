"""The application stylesheet, generated from the theme tokens (design spec 5.1, 5.3).

Font sizes are in pixels so they are the same on Windows scale factors and macOS.
Widgets opt into variants with dynamic properties, e.g. ``variant="primary"``.
"""

from __future__ import annotations

from .fonts import mono_family
from .icons import checkbox_url, file_url
from .tokens import Theme

_QSS = """
QWidget {{ font-size: 13px; color: {text_primary}; }}
QMainWindow, QWidget#pages, QWidget[page="true"] {{ background: {bg_window}; }}
QDialog {{ background: {bg_surface}; }}
QFrame#header {{ background: {bg_surface}; border: none; border-bottom: 1px solid {border_default}; }}
QToolButton[role="tab"] {{
    border: none; border-top: 2px solid transparent; border-bottom: 2px solid transparent;
    padding: 0 12px; height: 44px; color: {text_secondary}; font-weight: 500; background: transparent;
}}
QToolButton[role="tab"]:hover {{ color: {text_primary}; }}
QToolButton[role="tab"]:checked {{ color: {accent}; border-bottom-color: {accent}; font-weight: 600; }}
QToolButton[role="tab"]:disabled {{ color: {text_disabled}; }}

QFrame[card="true"] {{ background: {bg_surface}; border: 1px solid {border_default}; border-radius: 8px; }}
QFrame[card="true"] QLabel {{ background: transparent; }}
QFrame[band="true"] {{ background: {bg_sunken}; border: none; border-top: 1px solid {border_default};
    border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }}
QFrame[sunken="true"] {{ background: {bg_sunken}; border: 1px solid {border_subtle}; border-radius: 6px; }}
QFrame[divider="true"] {{ background: {border_subtle}; border: none; max-height: 1px; min-height: 1px; }}
QFrame[vdivider="true"] {{ background: {border_default}; border: none; max-width: 1px; min-width: 1px; }}
QFrame#summary {{ background: {bg_surface}; border: none; border-bottom: 1px solid {border_default}; }}
QWidget[surface="true"] {{ background: {bg_surface}; }}

QLabel[role="pageTitle"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="sampleName"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="dialogTitle"] {{ font-size: 17px; font-weight: 600; }}
QLabel[role="cardTitle"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="panelTitle"] {{ font-size: 13px; font-weight: 600; }}
QLabel[role="groupTitle"] {{ font-size: 13px; font-weight: 600; color: {text_primary}; }}
QLabel[role="secondary"] {{ color: {text_secondary}; }}
QLabel[role="hint"] {{ font-size: 12px; color: {text_tertiary}; }}
QLabel[role="caps"] {{ color: {text_tertiary}; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 12px; }}
QLabel[role="monoHint"] {{ font-family: "{mono}"; font-size: 11px; color: {text_tertiary}; }}
QLabel[role="error"] {{ font-size: 12px; color: {fail_fg}; }}
QLabel[role="brand"] {{ font-size: 14px; font-weight: 700; }}
QLabel[role="step"], QFrame[card="true"] QLabel[role="step"] {{
    background: {accent_subtle}; color: {accent_onSubtle}; border-radius: 11px;
    font-size: 12px; font-weight: 600; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }}
QLabel[role="defaultText"] {{ font-size: 12px; color: {text_tertiary}; }}
QLabel[role="defaultText"][changed="true"] {{ color: {accent}; }}

QPushButton, QToolButton[variant] {{
    background: {bg_surface}; border: 1px solid {border_strong}; border-radius: 6px; padding: 0 14px;
    min-height: 30px; color: {text_primary}; font-weight: 500;
}}
QPushButton:hover, QToolButton[variant]:hover {{ background: {bg_hover}; }}
QPushButton:pressed, QToolButton[variant]:pressed {{ background: {bg_pressed}; }}
QPushButton:disabled, QToolButton[variant]:disabled {{ color: {text_disabled}; border-color: {border_default}; }}
QPushButton:focus, QToolButton[variant]:focus {{ border: 2px solid {accent}; padding: 0 13px; }}
QPushButton[variant="primary"] {{ background: {accent}; border-color: {accent}; color: {text_onAccent};
    font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {accent_pressed}; }}
QPushButton[variant="primary"]:disabled {{ background: {primary_disabled}; border-color: {primary_disabled};
    color: {bg_surface}; }}
QPushButton[variant="primary"]:focus {{ border: 2px solid {accent_pressed}; }}
QPushButton[variant="ghost"] {{ border-color: transparent; background: transparent; color: {accent}; }}
QPushButton[variant="ghost"]:hover {{ background: {accent_subtle}; }}
QPushButton[variant="ghost"]:pressed {{ background: {accent_ghostPressed}; }}
QPushButton[variant="ghost"]:disabled {{ color: {text_disabled}; }}
QPushButton[variant="danger"] {{ border-color: {fail_buttonBorder}; color: {fail_title}; background: transparent; }}
QPushButton[variant="danger"]:hover {{ background: {fail_bg}; }}
QPushButton[variant="danger"]:pressed {{ background: {fail_pressed}; }}
QPushButton[size="small"], QToolButton[size="small"] {{ min-height: 24px; max-height: 26px; padding: 0 8px;
    font-size: 12px; }}
QPushButton[size="small"]:focus {{ padding: 0 7px; }}
QPushButton[size="large"] {{ min-height: 38px; padding: 0 18px; }}
QToolButton[variant="icon"] {{ padding: 0; min-width: 24px; max-width: 26px; min-height: 24px; max-height: 26px; }}
QToolButton[variant="flat"], QToolButton[variant="flat"]:focus {{ border: none; background: transparent;
    padding: 0 6px; }}
QToolButton[variant="flat"]:hover {{ background: {bg_hover}; border-radius: 6px; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QPushButton[variant="link"] {{ border: none; background: transparent; color: {accent}; padding: 0 4px;
    min-height: 18px; font-size: 12px; }}
QPushButton[variant="link"]:hover {{ text-decoration: underline; background: transparent; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {bg_surface}; border: 1px solid {border_strong}; border-radius: 6px; padding: 0 10px;
    min-height: 30px; color: {text_primary}; selection-background-color: {accent}; selection-color: {text_onAccent};
}}
QPlainTextEdit {{ padding: 6px 8px; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {border_hover}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border: 2px solid {accent}; padding: 0 9px; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {text_disabled}; background: {bg_sunken}; border-color: {border_default}; }}
QLineEdit[invalid="true"], QSpinBox[invalid="true"], QDoubleSpinBox[invalid="true"], QComboBox[invalid="true"] {{
    border: 1px solid {fail_solid}; }}
QLineEdit[mono="true"] {{ font-family: "{mono}"; font-size: 12px; }}
QLineEdit[search="true"] {{ padding-left: 28px; }}
QSpinBox, QDoubleSpinBox {{ padding-top: 0; padding-bottom: 0; padding-left: 8px; padding-right: 8px; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ padding-left: 7px; padding-right: 7px; }}
QSpinBox QLineEdit, QDoubleSpinBox QLineEdit, QSpinBox QLineEdit:focus, QDoubleSpinBox QLineEdit:focus {{
    border: none; padding: 0; min-height: 0; background: transparent; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 0px; height: 0px; border: none; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; }}
QComboBox::down-arrow:disabled {{ image: url("{chevron_disabled}"); }}
QComboBox QAbstractItemView {{ background: {bg_surface}; border: 1px solid {border_default}; padding: 4px;
    selection-background-color: {bg_selected}; selection-color: {text_primary}; outline: 0; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 14px; height: 14px; }}
QCheckBox::indicator:unchecked {{ image: url("{cb_off}"); }}
QCheckBox::indicator:unchecked:hover {{ image: url("{cb_off_hover}"); }}
QCheckBox::indicator:checked {{ image: url("{cb_on}"); }}
QCheckBox::indicator:unchecked:disabled {{ image: url("{cb_off_disabled}"); }}
QCheckBox::indicator:checked:disabled {{ image: url("{cb_on_disabled}"); }}
QCheckBox:focus {{ color: {accent}; }}
QCheckBox:disabled {{ color: {text_disabled}; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: {bg_sunken}; width: 12px; margin: 0; border: none; }}
QScrollBar:horizontal {{ background: {bg_sunken}; height: 12px; margin: 0; border: none; }}
QScrollBar::handle:vertical {{ background: {border_strong}; border-radius: 4px; min-height: 28px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border_strong}; border-radius: 4px; min-width: 28px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {border_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QToolTip {{ background: {tooltip_bg}; color: {tooltip_fg}; border: none; padding: 6px 8px; font-size: 12px; }}

QProgressBar {{ border: none; border-radius: 4px; background: {track}; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ border-radius: 4px; background: {accent}; }}

QTableView, QListView, QTreeView {{ background: {bg_surface}; border: none; outline: 0;
    selection-background-color: {bg_selected}; selection-color: {text_primary}; gridline-color: {border_subtle}; }}
QHeaderView {{ background: {bg_sunken}; border: none; }}
QHeaderView::section {{ background: {bg_sunken}; border: none; border-bottom: 1px solid {border_default};
    padding: 0 12px; height: 32px; color: {text_tertiary}; font-size: 11px; font-weight: 600; }}
QTableCornerButton::section {{ background: {bg_sunken}; border: none; }}

QMenu {{ background: {bg_surface}; border: 1px solid {border_default}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {bg_hover}; }}
QMenu::item:disabled {{ color: {text_disabled}; }}
QMenu::separator {{ height: 1px; background: {border_subtle}; margin: 4px 6px; }}
QMenuBar {{ background: {bg_surface}; }}

QSplitter::handle {{ background: transparent; }}
QMessageBox QLabel {{ font-size: 13px; }}
"""


def build_stylesheet(theme: Theme) -> str:
    tokens = dict(theme.qss_tokens)
    tokens["mono"] = mono_family()
    tokens["chevron"] = file_url("chevron-down", theme["text.secondary"])
    tokens["chevron_disabled"] = file_url("chevron-down", theme["text.disabled"])
    tokens["cb_off"] = checkbox_url(False, theme["bg.surface"], theme["border.strong"], "")
    tokens["cb_off_hover"] = checkbox_url(False, theme["bg.surface"], theme["border.hover"], "")
    tokens["cb_off_disabled"] = checkbox_url(False, theme["bg.sunken"], theme["border.default"], "")
    tokens["cb_on"] = checkbox_url(True, theme["accent"], theme["accent"], theme["text.onAccent"])
    tokens["cb_on_disabled"] = checkbox_url(True, theme["primary.disabled"], theme["primary.disabled"],
                                            theme["bg.surface"])
    return _QSS.format(**tokens)
