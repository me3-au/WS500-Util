# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Paul (nfbr.net)
"""
WS500 Util - PySide6 GUI for reviewing and editing Wakespeed config files.

Layout:
  - Left: sidebar with "Summary" + one entry per command found in the file.
  - Right: per-command page with a row for each field (name, current value,
    valid range, default, description). Voltage fields show both the
    system-voltage value (editable) and the file-stored 12V-normalized value.

Run:
    pip install PySide6
    python ws500_util.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QPoint, QRect, QSize, QRegularExpression
from PySide6.QtGui import QAction, QKeySequence, QFont, QPalette, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QListWidget, QListWidgetItem,
    QStackedWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QMessageBox, QPlainTextEdit, QScrollArea, QFrame, QGridLayout,
    QSizePolicy, QStatusBar, QSpinBox, QDoubleSpinBox, QCheckBox, QRadioButton,
    QButtonGroup, QLayout, QComboBox,
)

import ws_config as wc
import _version as ver


# ---------------------------------------------------------------------------
# Wheel-event-suppressing editor widgets. The default QSpinBox / QDoubleSpinBox
# / QComboBox all consume mouse-wheel events to change their value, which is
# infuriating when the user is trying to scroll the page and the cursor
# happens to pass over a field. These subclasses .ignore() the wheel event
# so it propagates up to the QScrollArea instead.
# ---------------------------------------------------------------------------


class _NoWheelMixin:
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    pass


class NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    pass


class NoWheelComboBox(_NoWheelMixin, QComboBox):
    pass


# ---------------------------------------------------------------------------
# FlowLayout: a horizontal layout that wraps onto new lines when there isn't
# enough width. Standard Qt example; used for bitmask checkbox rows and
# choices radio rows so they reflow as the window narrows.
# ---------------------------------------------------------------------------


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list = []

    def addItem(self, item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self, i): return self._items[i] if 0 <= i < len(self._items) else None
    def takeAt(self, i): return self._items.pop(i) if 0 <= i < len(self._items) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))
    def hasHeightForWidth(self): return True
    def heightForWidth(self, w): return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self): return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        s += QSize(m.left() + m.right(), m.top() + m.bottom())
        return s

    def _layout(self, rect, test_only):
        x, y, line_h, spc = rect.x(), rect.y(), 0, self.spacing()
        for it in self._items:
            sz = it.sizeHint()
            next_x = x + sz.width() + spc
            if next_x - spc > rect.right() and line_h > 0:
                x = rect.x()
                y = y + line_h + spc
                next_x = x + sz.width() + spc
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x = next_x
            line_h = max(line_h, sz.height())
        return y + line_h - rect.y()


# ---------------------------------------------------------------------------
# Sidebar grouping
# ---------------------------------------------------------------------------
# Order within each group is the order the user wants to see in the sidebar.
# Codes not listed in any group (and not hidden) fall into an "Other" group at
# the end so unexpected entries are never silently dropped from the UI.
SIDEBAR_GROUPS: list[tuple[str, list[str]]] = [
    ("System Config",    ["SCN", "SCO", "SCA", "SCT", "CNG", "CCN"]),
    ("Battery Charging", ["CPB", "CPA", "CPO", "CPF", "CPP", "CPE"]),
]
HIDDEN_CODES: set[str] = {"DEP"}


# ---------------------------------------------------------------------------
# Theme-aware colors
# ---------------------------------------------------------------------------
# Resolved once in main() after QApplication exists. Widgets read from this
# dict instead of hard-coding hex values so dark mode stays legible.
THEME: dict[str, str] = {}


def _resolve_theme() -> dict[str, str]:
    """Pick text colors based on the active palette's window background."""
    pal = QApplication.palette()
    is_dark = pal.color(QPalette.Window).lightness() < 128
    if is_dark:
        return {
            "muted":   "#cfcfcf",   # secondary text (range, default)
            "dim":     "#a8a8a8",   # tertiary text (file:, raw line, list)
            "strong":  "#e8e8e8",   # column headers, desc
            "err":     "#ff6b6b",
            "warn":    "#ffc78a",
            "sep":     "#3a3a3a",
            "err_border": "#ff6b6b",
            "dynamic": "#7cc4ff",   # dynamically-sourced text (cross-cmd refs)
            # CAN direction badges (Tx/Rx/Rx-Tx/Internal)
            "tx":      "#7ee787",
            "rx":      "#79c0ff",
            "rxtx":    "#d2a8ff",
            "internal":"#a8a8a8",
        }
    return {
        "muted":   "#555",
        "dim":     "#888",
        "strong":  "#333",
        "err":     "#b00",
        "warn":    "#a60",
        "sep":     "#ddd",
        "err_border": "#b00",
        "dynamic": "#0066cc",
        "tx":      "#22863a",
        "rx":      "#0366d6",
        "rxtx":    "#6f42c1",
        "internal":"#777",
    }


def _direction_badge_html(direction: str) -> str:
    """Render a colored '[Tx]' / '[Rx]' / '[Rx/Tx]' / '[Internal]' badge for
    inline use in a RichText description. Falls back to empty string for an
    unrecognized direction so the schema can use any value safely."""
    key = {"Tx": "tx", "Rx": "rx", "Rx/Tx": "rxtx",
           "Internal": "internal"}.get(direction)
    if not key:
        return ""
    return (f"<span style='color:{THEME[key]}; font-weight:bold; "
            f"font-family:monospace;'>[{direction}]</span> ")


# ---------------------------------------------------------------------------
# Field editor row
# ---------------------------------------------------------------------------


def _spec_decimals(spec: dict) -> int:
    return int(spec.get("decimals", 2))


def _format_file_number(value: float, spec: dict) -> str:
    """Format a numeric value into the string form we want to write to the file."""
    if spec.get("type") == "int":
        return str(int(round(value)))
    return f"{value:.{_spec_decimals(spec)}f}"


def _normalize_file_string(value: str, spec: dict) -> str:
    """Re-format the file's current value to match our save formatting so that
    page-load doesn't immediately read as "dirty". E.g. file '14.0' with
    decimals=2 normalizes to '14.00'.

    For 'as': 'text' fields, do NOT normalize - we explicitly want to preserve
    the literal source string (signed zero, leading zeros, etc.)."""
    if spec.get("as") == "text":
        return value
    t = spec.get("type")
    if t == "int":
        try:
            return str(int(value))
        except (ValueError, TypeError):
            return value
    if t == "float":
        try:
            return _format_file_number(float(value), spec)
        except (ValueError, TypeError):
            return value
    return value


class FieldRow(QWidget):
    """One row of a command page.

    Columns: Field | Current | New | Range | Default | Notes
    Description sits on a second row spanning all columns.

    'Current' shows the value already in the file (in display units for V
    fields, with the raw file-units shown in a tooltip). 'New' is the editor.

    Dependency handling: rows whose schema lists a 'disabled_when' clause are
    greyed out and forced to the field's default value when the controlling
    field has any of the listed values. The CommandPage that owns the rows
    drives this via set_disabled_by_dep().
    """

    valueChanged = Signal()  # emitted when user edits a field

    def __init__(self, field_spec: dict, file_value: str, sv: float, parent=None):
        super().__init__(parent)
        self.spec = field_spec
        self.sv = sv
        self.is_password = field_spec.get("sensitive") == "password"
        # All editors stay "clean" until the user actively interacts. This
        # protects against spinboxes silently normalizing literal file values
        # (e.g. '-0.00' -> '0.00' on QDoubleSpinBox load, which would otherwise
        # report dirty and lose the negative sign on Apply).
        self._user_touched = False
        self._disabled_by_dep = False
        # Resolve the kind of editor up-front so file_value/_set_editor_value
        # can dispatch by name instead of by isinstance.
        self._kind = self._resolve_kind(field_spec)
        # Bitmask state: preserves any bits in the file that aren't in the
        # schema's `bits` list, so unknown bits aren't dropped on save.
        self.bit_checks: list[tuple[int, QCheckBox]] = []
        self._unknown_bits = 0
        # Choices state (radio buttons for 'as': 'choices' fields).
        self.choice_radios: list[tuple[float, QRadioButton]] = []
        self.choice_group: QButtonGroup | None = None
        # Notes-column state. Kept as fields so a higher-priority message can
        # fully replace a lower-priority one without losing it: when the
        # higher one clears, _render_note() restores whichever lower state
        # is still active. Priority (top wins):
        #   _note_edit       - validation error or password-edit warning
        #   _note_constraint - cross-field constraint violation
        #   _note_disabled   - "disabled by ..." or "not in use ..."
        #   _note_info       - cross-command "Active: ..."
        #   (password kind)  - "File has ..." baseline status
        self._note_edit = ""
        self._note_edit_color = ""
        self._note_constraint = ""
        self._note_disabled = ""
        self._note_info = ""
        # Normalize the file value to our save formatting so the initial state
        # isn't immediately reported as dirty (e.g. '14.0' vs '14.00').
        self._file_value = _normalize_file_string(file_value, field_spec)

        layout = QGridLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setHorizontalSpacing(12)

        # All column labels word-wrap and have small minimum widths so the row
        # reflows gracefully as the window narrows. Editors (spinboxes etc.)
        # keep their own max widths; bitmask/choices editors use FlowLayout so
        # their inner buttons wrap onto extra lines instead of overflowing.

        # --- Field name -----------------------------------------------------
        self.name_lbl = QLabel(field_spec["name"])
        bold = QFont(); bold.setBold(True); self.name_lbl.setFont(bold)
        self.name_lbl.setWordWrap(True)
        self.name_lbl.setMinimumWidth(110)
        self.name_lbl.setMaximumWidth(220)
        self.name_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        # --- Current (display-form, read-only) ------------------------------
        # V-scaled fields render two lines: system-target voltage in bold and
        # the file (12V-normalized) value dim - so the user sees both the
        # 'target' and what gets saved. RichText is enabled to support that.
        self.current_lbl = QLabel(self._current_display_text(self._file_value))
        self.current_lbl.setTextFormat(Qt.RichText)
        self.current_lbl.setWordWrap(True)
        self.current_lbl.setMinimumWidth(120)
        self.current_lbl.setMaximumWidth(220)
        self.current_lbl.setStyleSheet(f"color: {THEME['strong']};")
        self.current_lbl.setToolTip(self._current_tooltip(self._file_value))
        self.current_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        # --- New (editor) ---------------------------------------------------
        self.editor = self._make_editor(field_spec, self._file_value, sv)
        # For V-scaled fields, also show a live-computed "file (12V-norm): X"
        # next to the editor so the user can see what will actually be written.
        self._file_value_lbl: QLabel | None = None
        if self.spec.get("scale") == "V" and self._kind == "float":
            self._editor_container = QWidget()
            ec = QHBoxLayout(self._editor_container)
            ec.setContentsMargins(0, 0, 0, 0)
            ec.setSpacing(6)
            ec.addWidget(self.editor)
            self._file_value_lbl = QLabel("")
            self._file_value_lbl.setStyleSheet(
                f"color: {THEME['dim']}; font-family: monospace;")
            ec.addWidget(self._file_value_lbl)
            ec.addStretch(1)
        else:
            self._editor_container = self.editor

        # --- Range / Default / Notes ---------------------------------------
        self.range_lbl = QLabel(self._range_text())
        self.range_lbl.setWordWrap(True)
        self.range_lbl.setStyleSheet(f"color: {THEME['muted']};")
        self.range_lbl.setMinimumWidth(100)
        self.range_lbl.setMaximumWidth(170)
        self.range_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.default_lbl = QLabel(self._default_label_text())
        self.default_lbl.setWordWrap(True)
        self.default_lbl.setStyleSheet(f"color: {THEME['muted']};")
        self.default_lbl.setMinimumWidth(80)
        self.default_lbl.setMaximumWidth(150)
        self.default_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.err_lbl = QLabel("")
        self.err_lbl.setWordWrap(True)
        self.err_lbl.setStyleSheet(f"color: {THEME['err']}; font-weight: bold;")
        self.err_lbl.setMinimumWidth(120)
        self.err_lbl.setMaximumWidth(260)
        self.err_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        # --- Description (rich text supports <b style='color:#c00'>...) ----
        # Capped at 800px so descriptions wrap onto multiple lines instead of
        # stretching across very wide windows. Word-wrap is still on so they
        # wrap earlier when the window is narrower.
        # `_desc_base` is the static text; `_desc_prefix` is dynamic and set
        # by CommandPage based on a controller field (see `prefix_from`).
        # If the schema sets `direction` (Tx/Rx/Rx-Tx/Internal), a colored
        # badge is prepended to _desc_base so the user can see at a glance
        # how this field interacts with the CAN bus.
        self._desc_base = (_direction_badge_html(field_spec.get("direction", ""))
                           + field_spec.get("desc", ""))
        self._desc_prefix = ""
        self.desc_lbl = QLabel(self._desc_base)
        self.desc_lbl.setTextFormat(Qt.RichText)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet(f"color: {THEME['strong']};")
        self.desc_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.desc_lbl.setMaximumWidth(800)
        self.desc_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        layout.addWidget(self.name_lbl,    0, 0)
        layout.addWidget(self.current_lbl, 0, 1)
        layout.addWidget(self._editor_container, 0, 2)
        layout.addWidget(self.range_lbl,   0, 3)
        layout.addWidget(self.default_lbl, 0, 4)
        layout.addWidget(self.err_lbl,     0, 5)
        layout.addWidget(self.desc_lbl,    1, 0, 1, 6)

        # Render the initial Notes-column state (password baseline if any).
        self._render_note()
        self._refresh_v_file_label()

    def _refresh_v_file_label(self) -> None:
        """For V-scaled float fields, update the live 'file (12V-norm)' label
        based on the editor's current value (or _file_value if untouched)."""
        if not self._file_value_lbl:
            return
        try:
            if self._user_touched:
                # Editor value -> divide by sv to get file form.
                v = self.editor.value() / self.sv if self.sv else self.editor.value()
            else:
                v = float(self._file_value)
        except (ValueError, TypeError, AttributeError):
            self._file_value_lbl.setText("")
            return
        self._file_value_lbl.setText(
            f"-> file: {v:.{_spec_decimals(self.spec)}f}")

    # -- editor factory ---------------------------------------------------

    @staticmethod
    def _resolve_kind(spec: dict) -> str:
        """Pick an editor kind: 'password' | 'bitmask' | 'checkbox' |
        'choices' | 'dropdown' | 'int' | 'float' | 'text' | 'string'."""
        if spec.get("sensitive") == "password":
            return "password"
        if spec.get("as") == "text":
            return "text"
        if spec.get("as") == "choices" and spec.get("choices"):
            return "choices"
        if spec.get("as") == "dropdown" and spec.get("choices"):
            return "dropdown"
        if spec.get("bits"):
            return "bitmask"
        t = spec.get("type", "string")
        if t == "int" and spec.get("min") == 0 and spec.get("max") == 1:
            return "checkbox"
        if t == "int":
            return "int"
        if t == "float":
            return "float"
        return "string"

    def _make_editor(self, spec: dict, file_value: str, sv: float):
        k = self._kind
        if k == "password":
            ed = QLineEdit()
            ed.setEchoMode(QLineEdit.Password)
            ed.setPlaceholderText("(blank = keep device password)")
            ed.setMaximumWidth(180)
            ed.textEdited.connect(lambda _t: self._on_edit())
            return ed
        if k == "bitmask":
            return self._make_bitmask_editor(spec, file_value)
        if k == "checkbox":
            cb = QCheckBox()
            try:
                cb.setChecked(bool(int(file_value)))
            except (ValueError, TypeError):
                cb.setChecked(bool(int(spec.get("default", 0))))
            cb.toggled.connect(lambda _c: self._on_edit())
            return cb
        if k == "text":
            ed = QLineEdit(file_value)
            ed.setMaximumWidth(160)
            ed.textEdited.connect(lambda _t: self._on_edit())
            return ed
        if k == "choices":
            return self._make_choices_editor(spec, file_value)
        if k == "dropdown":
            return self._make_dropdown_editor(spec, file_value)
        if k == "int":
            ed = NoWheelSpinBox()
            lo = int(spec.get("min", -2_147_483_648))
            hi = int(spec.get("max",  2_147_483_647))
            try:
                cur = int(file_value)
            except (ValueError, TypeError):
                cur = int(spec.get("default", 0))
            ed.setRange(min(lo, cur), max(hi, cur))
            ed.setValue(cur)
            ed.setMaximumWidth(140)
            ed.valueChanged.connect(lambda _v: self._on_edit())
            return ed
        if k == "float":
            ed = NoWheelDoubleSpinBox()
            decimals = _spec_decimals(spec)
            ed.setDecimals(decimals)
            ed.setSingleStep(10 ** -decimals)
            if spec.get("scale") == "V":
                lo = float(spec.get("min", 0.0)) * sv
                hi = float(spec.get("max", 0.0)) * sv
                try:
                    val = float(file_value) * sv
                except (ValueError, TypeError):
                    val = float(spec.get("default", 0.0)) * sv
            else:
                lo = float(spec.get("min", -1e9))
                hi = float(spec.get("max",  1e9))
                try:
                    val = float(file_value)
                except (ValueError, TypeError):
                    val = float(spec.get("default", 0.0))
            ed.setRange(min(lo, val), max(hi, val))
            ed.setValue(val)
            ed.setMaximumWidth(160)
            ed.valueChanged.connect(lambda _v: self._on_edit())
            return ed
        # string / csv_strings
        ed = QLineEdit(file_value)
        ml = spec.get("max_len")
        if ml is not None:
            ed.setMaxLength(int(ml))
        ed.setMaximumWidth(220)
        ed.textEdited.connect(lambda _t: self._on_edit())
        return ed

    def _make_bitmask_editor(self, spec: dict, file_value: str):
        widget = QWidget()
        h = FlowLayout(widget, margin=0, spacing=10)
        try:
            file_int = int(file_value)
        except (ValueError, TypeError):
            file_int = int(spec.get("default", 0))
        known_mask = 0
        for bit in spec["bits"]:
            v = int(bit["value"])
            known_mask |= v
            cb = QCheckBox(bit["label"])
            cb.setToolTip(f"bit value: {v}")
            cb.setChecked(bool(file_int & v))
            cb.toggled.connect(lambda _c: self._on_edit())
            h.addWidget(cb)
            self.bit_checks.append((v, cb))
        self._unknown_bits = file_int & ~known_mask
        # Live computed value of the OR'd bits, updates as the user toggles.
        self._bitmask_value_lbl = QLabel("")
        self._bitmask_value_lbl.setStyleSheet(
            f"color: {THEME['dynamic']}; font-family: monospace; font-weight: bold;")
        h.addWidget(self._bitmask_value_lbl)
        self._refresh_bitmask_label()
        return widget

    def _refresh_bitmask_label(self) -> None:
        if self._kind != "bitmask" or not getattr(self, "_bitmask_value_lbl", None):
            return
        val = self._unknown_bits
        for v, cb in self.bit_checks:
            if cb.isChecked():
                val |= v
        self._bitmask_value_lbl.setText(f"= {val}")

    def _make_dropdown_editor(self, spec: dict, file_value: str):
        """QComboBox driven by `choices`. Each entry stores its integer file
        value via setItemData. Values not in the choices list are added as a
        '(custom: N)' entry and selected, so the literal source value is
        preserved unless the user picks a known option."""
        cb = NoWheelComboBox()
        cb.setMaximumWidth(360)
        try:
            cur = int(file_value)
        except (ValueError, TypeError):
            try:
                cur = int(spec.get("default", 0))
            except (ValueError, TypeError):
                cur = 0
        matched = False
        for choice in spec["choices"]:
            cv = int(choice["value"])
            cb.addItem(f"{cv} - {choice['label']}", cv)
            if cv == cur and not matched:
                cb.setCurrentIndex(cb.count() - 1)
                matched = True
        if not matched:
            cb.addItem(f"(custom: {cur})", cur)
            cb.setCurrentIndex(cb.count() - 1)
        cb.currentIndexChanged.connect(lambda _i: self._on_edit())
        return cb

    def _make_choices_editor(self, spec: dict, file_value: str):
        """Render a row of radio buttons for an enumerated numeric field.
        Each label shows both the human name and the file value, e.g.
        '48V (4.00)', so the user sees what gets written. Values that don't
        match any choice leave all radios unchecked; the literal source value
        is preserved unless the user picks an option."""
        widget = QWidget()
        h = FlowLayout(widget, margin=0, spacing=10)
        self.choice_group = QButtonGroup(widget)
        self.choice_group.setExclusive(True)
        try:
            cur = float(file_value)
        except (ValueError, TypeError):
            try:
                cur = float(spec.get("default", 0))
            except (ValueError, TypeError):
                cur = 0.0
        decimals = _spec_decimals(spec)
        any_matched = False
        for choice in spec["choices"]:
            cv = float(choice["value"])
            label = choice["label"]
            value_str = (str(int(cv)) if spec.get("type") == "int"
                         else f"{cv:.{decimals}f}")
            rb = QRadioButton(f"{label}  ({value_str})")
            if abs(cv - cur) < 1e-6 and not any_matched:
                rb.setChecked(True)
                any_matched = True
            rb.toggled.connect(lambda _c: self._on_edit())
            self.choice_group.addButton(rb)
            h.addWidget(rb)
            self.choice_radios.append((cv, rb))
        if not any_matched:
            # Leave all radios unchecked. Need autoExclusive(False) momentarily
            # to allow the "no selection" state to actually take hold.
            self.choice_group.setExclusive(False)
            for _v, rb in self.choice_radios:
                rb.setAutoExclusive(False)
                rb.setChecked(False)
                rb.setAutoExclusive(True)
            self.choice_group.setExclusive(True)
        return widget

    # -- label helpers ----------------------------------------------------

    def _range_text(self) -> str:
        if self._kind == "checkbox":
            return "0 or 1 (checkbox)"
        if self._kind == "bitmask":
            return f"bitmask 0..{self.spec.get('max', 255)}"
        if self._kind == "dropdown":
            return f"enum ({len(self.spec.get('choices', []))} options)"
        t = self.spec.get("type", "string")
        if t in ("string", "csv_strings"):
            ml = self.spec.get("max_len")
            return f"string (<= {ml} chars)" if ml else "string"
        lo = self.spec.get("min")
        hi = self.spec.get("max")
        if self.spec.get("scale") == "V" and self.sv:
            try:
                return f"range: {float(lo) * self.sv:g} .. {float(hi) * self.sv:g} V"
            except (TypeError, ValueError):
                pass
        return f"range: {lo} .. {hi}"

    def _default_label_text(self) -> str:
        default = self.spec.get("default", "")
        if self._kind == "checkbox":
            return f"default: {'On' if str(default) == '1' else 'Off'}"
        if self._kind == "bitmask":
            return f"default: {default}"
        if self.spec.get("scale") == "V" and self.sv:
            try:
                d_disp = float(default) * self.sv
                return f"default: {d_disp:.{_spec_decimals(self.spec)}f}"
            except (TypeError, ValueError):
                pass
        return f"default: {default}"

    def _current_display_text(self, file_value: str) -> str:
        if self._kind == "password":
            if not file_value:
                return "(empty)"
            return "****" if file_value.startswith(".") else file_value
        if self._kind == "checkbox":
            try:
                return "On" if int(file_value) else "Off"
            except (ValueError, TypeError):
                return file_value
        if self._kind == "bitmask":
            try:
                fi = int(file_value)
            except (ValueError, TypeError):
                return file_value
            labels = [bit["label"] for bit in self.spec.get("bits", [])
                      if fi & int(bit["value"])]
            if labels:
                return f"{fi} ({', '.join(labels)})"
            return f"{fi}"
        if self._kind == "choices":
            try:
                fv = float(file_value)
            except (ValueError, TypeError):
                return file_value
            for choice in self.spec.get("choices", []):
                if abs(float(choice["value"]) - fv) < 1e-6:
                    return f"{file_value} ({choice['label']})"
            return f"{file_value} (custom)"
        if self._kind == "dropdown":
            try:
                fv = int(file_value)
            except (ValueError, TypeError):
                return file_value
            for c in self.spec.get("choices", []):
                if int(c["value"]) == fv:
                    return f"{file_value} ({c['label']})"
            return f"{file_value} (custom)"
        if self.spec.get("scale") == "V":
            try:
                decimals = _spec_decimals(self.spec)
                system_v = float(file_value) * self.sv
                # System Target Voltage (what the user thinks in), then a
                # dim sub-line showing the File Normalized Voltage (12V-norm).
                return (f"<b>{system_v:.{decimals}f}</b> V (system target)"
                        f"<br><span style='color:{THEME['dim']};'>"
                        f"file (12V-norm): {file_value}</span>")
            except (ValueError, TypeError):
                return file_value
        return file_value

    def _current_tooltip(self, file_value: str) -> str:
        if self._kind == "password":
            return f"file value: {file_value!r}"
        if self._kind == "bitmask":
            return f"file integer: {file_value}"
        if self.spec.get("scale") == "V":
            return f"file (12V-norm): {file_value}  ×  SV {self.sv:g}"
        if self.spec.get("scale") == "A":
            return f"file (500Ah-norm): {file_value}"
        return f"file: {file_value}"

    # -- editor I/O -------------------------------------------------------

    def _set_editor_value_silent(self, value: str) -> None:
        """Set the editor to a value without firing valueChanged."""
        # Block the editor itself plus any child checkboxes/radios.
        widgets = [self.editor]
        if self._kind == "bitmask":
            widgets.extend(cb for _v, cb in self.bit_checks)
        if self._kind == "choices":
            widgets.extend(rb for _v, rb in self.choice_radios)
        for w in widgets:
            w.blockSignals(True)
        try:
            if self._kind == "password":
                self.editor.setText("")
            elif self._kind == "checkbox":
                try:
                    self.editor.setChecked(bool(int(value)))
                except (ValueError, TypeError):
                    self.editor.setChecked(bool(int(self.spec.get("default", 0))))
            elif self._kind == "bitmask":
                try:
                    fi = int(value)
                except (ValueError, TypeError):
                    fi = int(self.spec.get("default", 0))
                # Preserve unknown bits in the file when re-setting.
                known_mask = 0
                for v, cb in self.bit_checks:
                    known_mask |= v
                    cb.setChecked(bool(fi & v))
                self._unknown_bits = fi & ~known_mask
                self._refresh_bitmask_label()
            elif self._kind == "choices":
                try:
                    target = float(value)
                except (ValueError, TypeError):
                    try:
                        target = float(self.spec.get("default", 0))
                    except (ValueError, TypeError):
                        target = 0.0
                matched = False
                for cv, rb in self.choice_radios:
                    if abs(cv - target) < 1e-6 and not matched:
                        rb.setChecked(True)
                        matched = True
                    else:
                        rb.setChecked(False)
            elif self._kind == "dropdown":
                try:
                    target = int(value)
                except (ValueError, TypeError):
                    target = int(self.spec.get("default", 0))
                idx = self.editor.findData(target)
                if idx >= 0:
                    self.editor.setCurrentIndex(idx)
                else:
                    self.editor.addItem(f"(custom: {target})", target)
                    self.editor.setCurrentIndex(self.editor.count() - 1)
            elif self._kind == "int":
                try:
                    self.editor.setValue(int(value))
                except (ValueError, TypeError):
                    self.editor.setValue(int(self.spec.get("default", 0)))
            elif self._kind == "float":
                try:
                    v = float(value)
                except (ValueError, TypeError):
                    v = float(self.spec.get("default", 0.0))
                if self.spec.get("scale") == "V":
                    v = v * self.sv
                self.editor.setValue(v)
                if self.spec.get("scale") == "V":
                    self._refresh_v_file_label()
            elif self._kind == "text":
                self.editor.setText(str(value))
            else:
                self.editor.setText(str(value))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _on_edit(self):
        if self._disabled_by_dep:
            return
        self._user_touched = True
        if self._kind == "password":
            txt = self.editor.text()
            ok, msg = wc.validate(txt, self.spec)
            if not ok:
                self._note_edit = msg
                self._note_edit_color = f"color: {THEME['err']}; font-weight: bold;"
                self.editor.setStyleSheet(f"border: 1px solid {THEME['err_border']};")
            elif txt:
                self._note_edit = "WILL SET DEVICE PASSWORD"
                self._note_edit_color = f"color: {THEME['warn']}; font-weight: bold;"
                self.editor.setStyleSheet(f"border: 1px solid {THEME['warn']};")
            else:
                self._note_edit = "Will clear password slot in file."
                self._note_edit_color = f"color: {THEME['warn']}; font-weight: bold;"
                self.editor.setStyleSheet("")
            self._render_note()
            self.valueChanged.emit()
            return
        if self._kind == "bitmask":
            self._refresh_bitmask_label()
        if self._kind == "float" and self.spec.get("scale") == "V":
            self._refresh_v_file_label()
        if self._kind == "string":
            ok, msg = wc.validate(self.editor.text(), self.spec)
            if not ok:
                self._note_edit = msg
                self._note_edit_color = f"color: {THEME['err']}; font-weight: bold;"
                self.editor.setStyleSheet(f"border: 1px solid {THEME['err_border']};")
            else:
                self._note_edit = ""
                self.editor.setStyleSheet("")
        elif self._kind == "text":
            txt = self.editor.text()
            ok, msg = self._validate_numeric_text(txt)
            if not ok:
                self._note_edit = msg
                self._note_edit_color = f"color: {THEME['err']}; font-weight: bold;"
                self.editor.setStyleSheet(f"border: 1px solid {THEME['err_border']};")
            else:
                self._note_edit = ""
                self.editor.setStyleSheet("")
        else:
            self._note_edit = ""
        self._render_note()
        self.valueChanged.emit()

    def _validate_numeric_text(self, txt: str) -> tuple[bool, str]:
        if txt.strip() == "":
            return False, "required"
        try:
            v = float(txt) if self.spec.get("type") == "float" else int(txt)
        except ValueError:
            return False, f"not a {self.spec.get('type', 'number')}"
        lo = self.spec.get("min")
        hi = self.spec.get("max")
        if lo is not None and v < lo:
            return False, f"< min ({lo})"
        if hi is not None and v > hi:
            return False, f"> max ({hi})"
        return True, ""

    # -- public API used by CommandPage ----------------------------------

    def file_value(self) -> tuple[str, bool]:
        """Return (file-form value string, is_valid).

        Priority:
          1. Disabled-by-dependency -> snap to schema default.
          2. Untouched non-string editor -> return literal _file_value so
             spinbox display quirks (e.g. -0.00 rendering as 0.00) can't
             silently change the saved value.
          3. Otherwise, read the editor.
        """
        if self._disabled_by_dep and not self.spec.get("preserve_when_disabled"):
            return _normalize_file_string(str(self.spec.get("default", "")), self.spec), True
        if not self._user_touched and self._kind not in ("string", "text"):
            return self._file_value, True

        if self._kind == "password":
            return self.editor.text(), True
        if self._kind == "checkbox":
            return ("1" if self.editor.isChecked() else "0"), True
        if self._kind == "bitmask":
            val = self._unknown_bits
            for v, cb in self.bit_checks:
                if cb.isChecked():
                    val |= v
            return str(val), True
        if self._kind == "choices":
            for cv, rb in self.choice_radios:
                if rb.isChecked():
                    return _format_file_number(cv, self.spec), True
            # No radio selected - preserve original (e.g. file value not in
            # the choices list).
            return self._file_value, True
        if self._kind == "dropdown":
            data = self.editor.currentData()
            return (str(int(data)) if data is not None else self._file_value), True
        if self._kind == "int":
            return str(self.editor.value()), True
        if self._kind == "float":
            v = self.editor.value()
            if self.spec.get("scale") == "V":
                v = v / self.sv if self.sv else v
            return _format_file_number(v, self.spec), True
        if self._kind == "text":
            raw = self.editor.text()
            ok, _ = self._validate_numeric_text(raw)
            return raw, ok
        # string
        raw = self.editor.text()
        ok, _ = wc.validate(raw, self.spec)
        return raw, ok

    def is_dirty(self) -> bool:
        new_file, _ = self.file_value()
        return new_file != self._file_value

    def set_disabled_by_dep(self, disabled: bool, reason: str = "") -> None:
        """Grey out (or restore) this row based on a controller field's value.

        Default behavior: editor locked, value snaps to schema default.

        If the schema sets `preserve_when_disabled: true`, the row stays
        EDITABLE and KEEPS its current value (visual still shows it's not in
        active use). Used for safety-critical fields like Alt Derate (half)
        where 'disabled by trigger' doesn't mean the value is harmless - a
        mistake here can destroy the alternator if Half-Power ever engages.
        """
        preserve = bool(self.spec.get("preserve_when_disabled", False))
        if disabled == self._disabled_by_dep:
            if disabled and not preserve:
                self._set_editor_value_silent(str(self.spec.get("default", "")))
            return
        self._disabled_by_dep = disabled
        lock_editor = disabled and not preserve
        self.editor.setEnabled(not lock_editor)
        if self._kind == "bitmask":
            for _v, cb in self.bit_checks:
                cb.setEnabled(not lock_editor)
        if self._kind == "choices":
            for _v, rb in self.choice_radios:
                rb.setEnabled(not lock_editor)
        dim = f"color: {THEME['dim']};"
        if disabled:
            if not preserve:
                self._set_editor_value_silent(str(self.spec.get("default", "")))
                self._note_disabled = f"disabled by {reason}" if reason else "disabled"
            else:
                self._note_disabled = (
                    f"not in use ({reason}) - kept editable for safety"
                    if reason else "not in use - kept editable for safety")
            for lbl in (self.name_lbl, self.current_lbl, self.range_lbl,
                        self.default_lbl, self.desc_lbl):
                lbl.setStyleSheet(dim)
        else:
            self._note_disabled = ""
            self.name_lbl.setStyleSheet("")
            self.current_lbl.setStyleSheet(
                f"color: {THEME['dim']}; font-family: monospace;")
            self.range_lbl.setStyleSheet(f"color: {THEME['muted']};")
            self.default_lbl.setStyleSheet(f"color: {THEME['muted']};")
            self.desc_lbl.setStyleSheet(f"color: {THEME['strong']};")
        self._render_note()

    def _password_status(self, file_value: str) -> tuple[str, str]:
        """Describe what the file currently holds for the Reg Password field.
        Returns (text, css color). Three states:
          - empty   -> "File has no password" (muted)
          - "1234"  -> "File has default password" (warn - this is the factory
                       default per guide v2.6.1 p.7; loading it to a device
                       overwrites whatever password is already set)
          - other   -> "File has custom password" (warn). Dot-prefixed values
                       (`.foo` = NPC-hidden) still count as custom.
        """
        if not file_value:
            return "File has no password", THEME["dim"]
        if file_value == "1234":
            return "File has default password (1234)", THEME["err"]
        return "File has custom password", THEME["warn"]

    def _render_note(self) -> None:
        """Render the Notes column based on priority-ordered state. See the
        comment in __init__ for the priority list."""
        if self._note_edit:
            self.err_lbl.setText(self._note_edit)
            self.err_lbl.setStyleSheet(self._note_edit_color)
            return
        if self._note_constraint:
            self.err_lbl.setText(self._note_constraint)
            self.err_lbl.setStyleSheet(f"color: {THEME['err']}; font-weight: bold;")
            return
        if self._note_disabled:
            self.err_lbl.setText(self._note_disabled)
            self.err_lbl.setStyleSheet(f"color: {THEME['dim']}; font-style: italic;")
            return
        if self._note_info:
            self.err_lbl.setText(self._note_info)
            self.err_lbl.setStyleSheet(f"color: {THEME['muted']}; font-style: italic;")
            return
        if self._kind == "password":
            text, color = self._password_status(self._file_value)
            self.err_lbl.setText(text)
            self.err_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")
            return
        self.err_lbl.setText("")
        self.err_lbl.setStyleSheet(f"color: {THEME['err']}; font-weight: bold;")

    def set_desc_prefix(self, prefix: str) -> None:
        """Prepend dynamic text (e.g. 'Alt Shunt - ...') to the description.
        Rendered in the theme's 'dynamic' color so the user can tell it's
        live cross-command content, not fixed text. Idempotent."""
        if prefix == self._desc_prefix:
            return
        self._desc_prefix = prefix
        if prefix:
            styled = (f"<span style='color:{THEME['dynamic']};'>{prefix}</span>")
        else:
            styled = ""
        self.desc_lbl.setText(styled + self._desc_base)

    def set_constraint_error(self, msg: str) -> None:
        """Set or clear a cross-field constraint violation message. For rows
        disabled-by-dep without `preserve_when_disabled`, the value is snapped
        to default so the constraint won't meaningfully fail - we ignore
        attempts to set a constraint message in that case."""
        if self._disabled_by_dep and not self.spec.get("preserve_when_disabled"):
            return
        self._note_constraint = msg
        self._render_note()

    def set_info_text(self, text: str) -> None:
        """Set or clear the cross-command 'Active: ...' annotation."""
        self._note_info = text
        self._render_note()

    # Read-only accessors for CommandPage's password-confirm logic. Avoid
    # callers reaching into _user_touched / _file_value directly.
    @property
    def user_touched(self) -> bool:
        return self._user_touched

    @property
    def original_file_value(self) -> str:
        return self._file_value

    def pending_password(self) -> str:
        """Current text in the password editor. Only meaningful for password
        kind rows; raises for any other kind so misuse is loud."""
        if self._kind != "password":
            raise ValueError("pending_password() only valid on password rows")
        return self.editor.text()

    def reset_to(self, new_file_value: str) -> None:
        """Re-baseline the row to a new file value (after Apply, or after an
        external edit such as the Summary name field syncing into $SCN)."""
        normalized = _normalize_file_string(new_file_value, self.spec)
        self._file_value = normalized
        self._user_touched = False
        self._note_edit = ""
        self._set_editor_value_silent(normalized)
        self.current_lbl.setText(self._current_display_text(normalized))
        self.current_lbl.setToolTip(self._current_tooltip(normalized))
        if isinstance(self.editor, QLineEdit):
            self.editor.setStyleSheet("")
        self._render_note()


# ---------------------------------------------------------------------------
# Command page
# ---------------------------------------------------------------------------


class CommandPage(QWidget):
    """A scrollable list of FieldRow plus an Apply button."""

    dirtyChanged = Signal()
    # Emitted whenever any row changes (pre-Apply). MainWindow fans this out
    # across pages so cross-command dependencies see the new value.
    pageValueChanged = Signal()

    # Set by MainWindow.rebuild_pages() so cross-command dependencies can query
    # field values in other commands. Signature: (cmd_code, field_name) ->
    # (value_string | None, spec | None).
    global_lookup = None

    def __init__(self, cmd: wc.CommandLine, schema: dict, sv: float, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.schema = schema   # the command spec from ws_schema.json
        self.sv = sv
        self.rows: list[FieldRow] = []

        outer = QVBoxLayout(self)
        title = QLabel(f"${cmd.code}{':' + str(cmd.profile) if cmd.profile is not None else ':'}  -  {schema.get('title', cmd.code)}")
        tf = QFont()
        tf.setPointSize(14)
        tf.setBold(True)
        title.setFont(tf)
        outer.addWidget(title)

        if schema.get("summary"):
            sub = QLabel(schema["summary"])
            sub.setStyleSheet(f"color: {THEME['muted']};")
            outer.addWidget(sub)

        raw = QLabel("Raw line: " + cmd.raw_original.strip())
        raw.setStyleSheet(f"color: {THEME['dim']}; font-family: monospace;")
        raw.setWordWrap(True)
        outer.addWidget(raw)

        # Field rows in a scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        v = QVBoxLayout(container)
        v.setSpacing(2)

        # Column header (Field | Current | New | Range | Default | Notes).
        # Match the FieldRow's minimum widths so headers track when the window
        # narrows. Each header label is left-aligned and word-wraps.
        hdr = QFrame()
        h = QGridLayout(hdr)
        h.setContentsMargins(4, 2, 4, 2)
        hdr_widths = [(110, 220), (80, 180), (140, 380), (100, 170), (80, 150), (120, 260)]
        for col, (text, (mn, mx)) in enumerate(zip(
                ["Field", "Current", "New", "Valid Range", "Default", "Notes"], hdr_widths)):
            lab = QLabel(text)
            lab.setStyleSheet(f"font-weight: bold; color: {THEME['strong']};")
            lab.setMinimumWidth(mn)
            lab.setMaximumWidth(mx)
            h.addWidget(lab, 0, col)
        v.addWidget(hdr)

        fields = schema.get("fields", [])
        # Maps field name -> FieldRow, for dependency lookups.
        self._row_by_name: dict[str, FieldRow] = {}
        for i, spec in enumerate(fields):
            file_val = cmd.values[i] if i < len(cmd.values) else str(spec.get("default", ""))
            row = FieldRow(spec, file_val, sv)
            row.valueChanged.connect(self._on_row_changed)
            self.rows.append(row)
            self._row_by_name[spec["name"]] = row
            v.addWidget(row)
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet(f"color: {THEME['sep']};")
            v.addWidget(sep)

        # Apply initial dependency / constraint / dynamic-prefix state.
        self._refresh_dependencies()
        self._refresh_constraints()
        self._refresh_dynamic_prefixes()

        # If file had more values than schema (forward-compatible), show them too
        extra = len(cmd.values) - len(fields)
        if extra > 0:
            note = QLabel(f"Note: file has {extra} additional value(s) not described in schema:")
            note.setStyleSheet(f"color: {THEME['warn']};")
            v.addWidget(note)
            for j in range(len(fields), len(cmd.values)):
                v.addWidget(QLabel(f"  [{j}] = {cmd.values[j]}"))

        v.addStretch(1)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.apply_btn = QPushButton("Apply to file")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setEnabled(False)
        btn_row.addWidget(self.apply_btn)
        outer.addLayout(btn_row)

    def _on_row_changed(self):
        self._refresh_dependencies()
        self._refresh_constraints()
        self._refresh_dynamic_prefixes()
        self.apply_btn.setEnabled(any(r.is_dirty() for r in self.rows))
        self.pageValueChanged.emit()

    def _refresh_dynamic_prefixes(self) -> None:
        """Update the description prefix on rows whose schema defines either
        `prefix_from` (static map) or `rpm_bucket` (computed RPM range)."""
        for row in self.rows:
            prefix = ""
            # Static map: prefix_from
            pf = row.spec.get("prefix_from")
            if pf:
                val, _ = self._lookup_value_and_spec(
                    pf.get("command", self.cmd.code), pf["field"])
                if val is not None:
                    prefix = pf.get("map", {}).get(str(val), "")
            # Computed: rpm_bucket (for RFM1..8 ranges from RFM_RPM)
            rb = row.spec.get("rpm_bucket")
            if rb:
                ctrl_val, _ = self._lookup_value_and_spec(
                    rb.get("command", self.cmd.code), rb["field"])
                if ctrl_val is not None:
                    bucket_prefix = wc.rpm_bucket_prefix(ctrl_val, int(rb["index"]))
                    if bucket_prefix:
                        prefix = bucket_prefix + prefix
            row.set_desc_prefix(prefix)

    def _refresh_constraints(self) -> None:
        """Cross-field constraints. Currently supports `max_field`: this row's
        value must be strictly less than the named field's value."""
        for row in self.rows:
            mf = row.spec.get("max_field")
            if not mf:
                continue
            ctrl = self._row_by_name.get(mf)
            if ctrl is None:
                continue
            try:
                rv = float(row.file_value()[0])
                cv = float(ctrl.file_value()[0])
            except (ValueError, TypeError):
                row.set_constraint_error("")
                continue
            if rv >= cv:
                row.set_constraint_error(f"must be < {mf} ({cv:g})")
            else:
                row.set_constraint_error("")

    def _refresh_dependencies(self) -> None:
        """For every row with a 'disabled_when' clause, evaluate the condition
        and toggle the row's disabled state. Supports three forms:

          1. Simple (same-command):
             {"field": "<name>", "values": [v1, v2, ...]}
          2. All-of (disabled when ALL listed conditions match):
             {"all_of": [cond, cond, ...]}
          3. Any-of (disabled when ANY listed condition matches):
             {"any_of": [cond, cond, ...]}

        Each condition takes:
          {"command": "<code>"?, "field": "<name>", "values"|"not_values": [...],
           "active_label": "<text>"?}

        When omitted, "command" defaults to this page's command.
        """
        for row in self.rows:
            dep = row.spec.get("disabled_when")
            if not dep:
                continue
            if "all_of" in dep:
                match, reasons, active = self._eval_conditions(dep["all_of"], mode="all")
            elif "any_of" in dep:
                match, reasons, active = self._eval_conditions(dep["any_of"], mode="any")
            else:
                match, reasons, active = self._eval_conditions([dep], mode="any")
            row.set_disabled_by_dep(match, reason=" AND ".join(reasons))
            row.set_info_text("Active: " + ", ".join(active) if (not match and active) else "")

    def _eval_conditions(self, conditions: list[dict], mode: str) -> tuple[bool, list[str], list[str]]:
        """Evaluate a list of conditions. Returns (overall_match, reasons,
        active_labels).

        - reasons: human-readable strings describing matched (disabling) conditions.
        - active_labels: labels of conditions that did NOT match (i.e., that
          are 'enabling' the row). Useful for showing 'Active: X, Y' info
          when at least one trigger is on.
        """
        reasons: list[str] = []
        active: list[str] = []
        per_cond_match: list[bool] = []
        for cond in conditions:
            cmd_code = cond.get("command", self.cmd.code)
            field_name = cond["field"]
            val, spec = self._lookup_value_and_spec(cmd_code, field_name)
            check_spec = spec or {"type": "string"}
            matched = False
            if val is not None:
                if "not_values" in cond:
                    matched = not any(
                        wc.values_equal(val, v, check_spec) for v in cond["not_values"]
                    )
                elif "values" in cond:
                    matched = any(
                        wc.values_equal(val, v, check_spec) for v in cond["values"]
                    )
                elif "gt" in cond or "gte" in cond or "lt" in cond or "lte" in cond:
                    matched = wc.compare_numeric(val, cond)
            per_cond_match.append(matched)
            label = cond.get("active_label", field_name)
            if matched:
                reasons.append(f"{field_name}={val}")
            else:
                # Condition didn't match -> this controller is "enabling" the row.
                if val is not None:
                    active.append(f"{label}={val}")
                else:
                    active.append(label)
        if mode == "all":
            # all([]) is True, but "all-of with no conditions" shouldn't
            # disable a row that has no rule. Guard with bool(...).
            overall = bool(per_cond_match) and all(per_cond_match)
        else:
            # any([]) is False, which is the intended behavior for "any-of
            # with no conditions" - no disabling condition matches.
            overall = any(per_cond_match)
        return overall, reasons, active

    def _lookup_value_and_spec(self, cmd_code: str, field_name: str) -> tuple[str | None, dict | None]:
        if cmd_code == self.cmd.code:
            ctrl = self._row_by_name.get(field_name)
            if ctrl is not None:
                v, _ = ctrl.file_value()
                return v, ctrl.spec
            return None, None
        if self.global_lookup is None:
            return None, None
        return self.global_lookup(cmd_code, field_name)

    def _apply(self):
        # Validate everything
        new_values: list[str] = []
        bad: list[str] = []
        for r in self.rows:
            v, ok = r.file_value()
            if not ok:
                bad.append(r.spec["name"])
            new_values.append(v)
        # Cross-field constraints (max_field: this < that)
        name_to_val = {r.spec["name"]: v for r, v in zip(self.rows, new_values)}
        for r in self.rows:
            mf = r.spec.get("max_field")
            if mf and mf in name_to_val:
                try:
                    if float(name_to_val[r.spec["name"]]) >= float(name_to_val[mf]):
                        bad.append(f"{r.spec['name']} (must be < {mf})")
                except (ValueError, TypeError):
                    pass
        if bad:
            QMessageBox.warning(self, "Validation errors",
                                "Fix these fields before applying:\n  - " + "\n  - ".join(bad))
            return
        # Confirm before applying a password change. The user has to actively
        # type a value, but they should still confirm before that value gets
        # written into the config.
        pw_rows = [r for r in self.rows if r.is_password and r.user_touched]
        if pw_rows:
            new_pw = pw_rows[0].pending_password()
            file_pw = pw_rows[0].original_file_value
            if new_pw:
                msg = (f"You are about to write a password value into the config file.\n\n"
                       f"When this file is loaded onto a regulator, the device's "
                       f"password will be set to:\n\n    {new_pw}\n\n"
                       f"To leave the existing device password unchanged, "
                       f"clear the field instead.\n\nProceed?")
            else:
                msg = (f"You are about to clear the password value in this file.\n\n"
                       f"The current value in the file ({file_pw!r}) will be removed. "
                       f"On the device, this means the existing password will "
                       f"NOT be changed (per the v2.6.1 guide).\n\nProceed?")
            ret = QMessageBox.warning(self, "Confirm password change", msg,
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return

        # preserve any extra trailing values
        if len(self.cmd.values) > len(new_values):
            new_values += self.cmd.values[len(new_values):]
        self.cmd.values = new_values
        self.cmd.dirty = True
        # refresh row baselines so they stop showing dirty
        for r, v in zip(self.rows, new_values):
            r.reset_to(v)
        self.apply_btn.setEnabled(False)
        self.dirtyChanged.emit()


# ---------------------------------------------------------------------------
# Summary page
# ---------------------------------------------------------------------------


class SummaryPage(QWidget):
    """Top-level page. Hosts the editable configuration name, which mirrors
    the $SCN Reg Name field, the window title, and the Save As default name.
    """

    nameEdited = Signal(str)  # user-typed new name (validated for chars/length)
    headerApplied = Signal()  # user clicked Apply to save header edits

    def __init__(self, cf: wc.ConfigFile, schema_by_code: dict, parent=None):
        super().__init__(parent)
        self.cf = cf
        self.schema_by_code = schema_by_code

        v = QVBoxLayout(self)

        title = QLabel("Summary")
        tf = QFont(); tf.setPointSize(16); tf.setBold(True)
        title.setFont(tf)
        v.addWidget(title)

        # Editable configuration name. Syncs with $SCN Reg Name and the Save As
        # default filename.
        scn = cf.find("SCN")
        initial = scn.values[1] if scn and len(scn.values) > 1 else ""

        name_row = QHBoxLayout()
        name_lbl = QLabel("Configuration name:")
        nf = QFont(); nf.setBold(True); name_lbl.setFont(nf)
        name_row.addWidget(name_lbl)

        self.name_edit = QLineEdit(initial)
        self.name_edit.setMaxLength(18)
        self.name_edit.setMaximumWidth(220)
        # Reject spaces, commas, '@' at the source instead of accepting then
        # silently failing to sync - keeps the editor and the model in lockstep.
        self.name_edit.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"[^ ,@]{0,18}")))
        self.name_edit.textEdited.connect(self._on_name_typed)
        name_row.addWidget(self.name_edit)

        self.name_err = QLabel("")
        self.name_err.setStyleSheet(f"color: {THEME['err']}; font-weight: bold;")
        name_row.addWidget(self.name_err)

        hint = QLabel("(syncs $SCN Reg Name, window title, and Save As filename)")
        hint.setStyleSheet(f"color: {THEME['muted']};")
        name_row.addWidget(hint)
        name_row.addStretch(1)
        v.addLayout(name_row)

        # Path + voltage info
        self.path_lbl = QLabel(f"File: {cf.path}" if cf.path else "File: (unsaved)")
        self.path_lbl.setStyleSheet(f"color: {THEME['dim']};")
        v.addWidget(self.path_lbl)
        v.addWidget(QLabel(f"System voltage multiplier (from $SCO): x{cf.sv_multiplier:g}"))
        compat = QLabel(
            f"App {ver.APP_NAME} {ver.VERSION}  -  schema targets "
            f"{ver.GUIDE_TITLE} v{ver.GUIDE_VERSION}")
        compat.setStyleSheet(f"color: {THEME['muted']};")
        v.addWidget(compat)

        # Header notes - editable. Click "Apply to file" to commit + save.
        notes_hdr = QHBoxLayout()
        notes_hdr.addWidget(QLabel("Header notes (saved with the file):"))
        notes_hdr.addStretch(1)
        gen_btn = QPushButton("Generate New Summary")
        gen_btn.setToolTip("Replace the notes with an auto-generated summary "
                           "of the config name, today's date, and every non-default field.")
        gen_btn.clicked.connect(self._generate_summary)
        notes_hdr.addWidget(gen_btn)
        v.addLayout(notes_hdr)

        self._header_initial = "\n".join(cf.header_comments())
        self.notes = QPlainTextEdit(self._header_initial)
        self.notes.setMaximumHeight(260)
        self.notes.setFont(QFont("Courier New"))
        self.notes.textChanged.connect(self._on_notes_changed)
        v.addWidget(self.notes)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.apply_btn = QPushButton("Apply to file")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(self.apply_btn)
        v.addLayout(btn_row)

        v.addStretch(1)

    def _on_notes_changed(self):
        self.apply_btn.setEnabled(self.notes.toPlainText() != self._header_initial)

    def _apply(self):
        raw = self.notes.toPlainText()
        new_text = wc.canonicalize_header(raw)
        self.cf.set_header_block(new_text)
        if new_text != raw:
            self.notes.blockSignals(True)
            self.notes.setPlainText(new_text)
            self.notes.blockSignals(False)
        self._header_initial = new_text
        self.apply_btn.setEnabled(False)
        self.headerApplied.emit()

    def _on_name_typed(self, text: str) -> None:
        # The QRegularExpressionValidator on name_edit already rejects spaces,
        # commas, '@', and over-length input at the keystroke. Anything that
        # gets here is valid - just emit.
        self.name_err.setText("")
        self.nameEdited.emit(text)

    def set_name_silent(self, name: str) -> None:
        """Update the name field without re-emitting nameEdited."""
        self.name_edit.blockSignals(True)
        self.name_edit.setText(name)
        self.name_edit.blockSignals(False)
        self.name_err.setText("")

    def set_path(self, path: Path | None) -> None:
        self.path_lbl.setText(f"File: {path}" if path else "File: (unsaved)")

    # -- auto-generated summary ------------------------------------------

    def _generate_summary(self) -> None:
        """Build a fresh header block from the config name, today's date, and
        every field whose current value differs from its schema default."""
        sv = self.cf.sv_multiplier
        name = self.name_edit.text() or "(unnamed)"
        today = date.today().isoformat()
        out: list[str] = [
            f"# Configuration: {name}",
            f"# Generated: {today}",
            "#",
            "# Non-default settings:",
        ]
        any_non_default = False
        for cmd in self.cf.commands:
            spec = self.schema_by_code.get(cmd.code)
            if not spec:
                continue
            non_default: list[str] = []
            for i, fspec in enumerate(spec.get("fields", [])):
                if i >= len(cmd.values):
                    continue
                val = cmd.values[i]
                if self._is_default(fspec, val):
                    continue
                non_default.append(
                    f"#   {fspec['name']}: {self._format_human(fspec, val, sv)}"
                    f"   (default: {self._format_human(fspec, str(fspec.get('default', '')), sv)})"
                )
            if non_default:
                any_non_default = True
                cmd_id = f"${cmd.code}" + (f":{cmd.profile}" if cmd.profile is not None else "")
                title = spec.get("title", cmd.code)
                out.append("#")
                out.append(f"# {cmd_id}  -  {title}")
                out.extend(non_default)
        if not any_non_default:
            out.append("#   (all fields at default)")
        out.append("#")
        self.notes.setPlainText("\n".join(out))

    @staticmethod
    def _is_default(spec: dict, file_value: str) -> bool:
        """Compare a raw file value to the schema default with type awareness."""
        default = spec.get("default", "")
        t = spec.get("type")
        if spec.get("sensitive") == "password":
            return file_value == "" and str(default) == ""
        if t == "int":
            try:
                return int(file_value) == int(default)
            except (ValueError, TypeError):
                return str(file_value).strip() == str(default).strip()
        if t == "float":
            try:
                return abs(float(file_value) - float(default)) < 1e-9
            except (ValueError, TypeError):
                return str(file_value).strip() == str(default).strip()
        return str(file_value).strip() == str(default).strip()

    @staticmethod
    def _format_human(spec: dict, file_value: str, sv: float) -> str:
        """Friendly rendering of a file value for the auto-summary."""
        if spec.get("sensitive") == "password":
            if not file_value:
                return "(none)"
            if file_value.startswith("."):
                return "(hidden)"
            return "(set)"
        if spec.get("bits"):
            try:
                fi = int(file_value)
            except (ValueError, TypeError):
                return file_value
            labels = [b["label"] for b in spec["bits"] if fi & int(b["value"])]
            return f"{fi} ({', '.join(labels)})" if labels else str(fi)
        if spec.get("as") == "choices" and spec.get("choices"):
            try:
                fv = float(file_value)
            except (ValueError, TypeError):
                return file_value
            for c in spec["choices"]:
                if abs(float(c["value"]) - fv) < 1e-6:
                    return f"{c['label']}"
            return f"{file_value} (custom)"
        t = spec.get("type")
        if t == "int" and spec.get("min") == 0 and spec.get("max") == 1:
            try:
                return "On" if int(file_value) else "Off"
            except (ValueError, TypeError):
                return file_value
        if spec.get("scale") == "V" and sv:
            try:
                decimals = _spec_decimals(spec)
                return f"{float(file_value) * sv:.{decimals}f} V"
            except (ValueError, TypeError):
                return file_value
        if t == "float":
            try:
                decimals = _spec_decimals(spec)
                return f"{float(file_value):.{decimals}f}"
            except (ValueError, TypeError):
                return file_value
        return file_value


# ---------------------------------------------------------------------------
# Preview page - live render of what the file looks like on disk
# ---------------------------------------------------------------------------


class PreviewPage(QWidget):
    """Read-only render of the current ConfigFile as it would be written to
    disk. Updates on demand via refresh()."""

    def __init__(self, cf: wc.ConfigFile, parent=None):
        super().__init__(parent)
        self.cf = cf
        v = QVBoxLayout(self)
        title = QLabel("File Preview")
        tf = QFont(); tf.setPointSize(16); tf.setBold(True); title.setFont(tf)
        v.addWidget(title)
        hint = QLabel("Live view of the file as it would be written to disk. "
                      "Reflects committed (applied) edits.")
        hint.setStyleSheet(f"color: {THEME['muted']};")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.text = QPlainTextEdit("")
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Courier New"))
        v.addWidget(self.text, 1)
        self.refresh()

    def refresh(self) -> None:
        try:
            self.text.setPlainText(wc.render_file(self.cf))
        except Exception as e:
            self.text.setPlainText(f"<render error: {e}>")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self, schema: dict):
        super().__init__()
        self.setWindowTitle(f"{ver.APP_NAME} {ver.VERSION}")
        self.resize(1280, 800)
        self.schema_full = schema
        self.schema_by_code = wc.schema_by_code(schema)
        self.cf: wc.ConfigFile | None = None
        self.pages: list[CommandPage] = []
        self.summary_page: SummaryPage | None = None
        self.preview_page: PreviewPage | None = None
        self.row_to_stack: list[int | None] = []

        # menus
        # File menu: just Quit. "Apply to file" on each command page does the
        # save - there is no separate save action.
        m_file = self.menuBar().addMenu("&File")
        a_quit = QAction("&Quit", self); a_quit.setShortcut(QKeySequence.Quit); a_quit.triggered.connect(self.close)
        m_file.addAction(a_quit)
        m_help = self.menuBar().addMenu("&Help")
        a_about = QAction("&About", self); a_about.triggered.connect(self._show_about)
        m_help.addAction(a_about)

        # body
        split = QSplitter()
        self.sidebar = QListWidget()
        self.sidebar.currentRowChanged.connect(self.on_page_change)
        self.stack = QStackedWidget()
        split.addWidget(self.sidebar)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 1040])
        self.setCentralWidget(split)

        self.status = QStatusBar(); self.setStatusBar(self.status)
        self.status.showMessage("Open a WS config file to begin.")

    # -- file ops ----------------------------------------------------------

    def load(self, path: str):
        try:
            cf = wc.parse_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Parse error", str(e))
            return
        self.cf = cf
        self.rebuild_pages()
        self._update_title()
        self.status.showMessage(
            f"Loaded {len(cf.commands)} command lines. SV multiplier x{cf.sv_multiplier:g}."
        )

    def save_file(self):
        if not self.cf:
            return
        if not self.cf.path:
            return self.save_file_as()
        try:
            wc.save_file(self.cf, self.cf.path)
        except Exception as e:
            QMessageBox.critical(self, "Save error", str(e))
            return
        self.status.showMessage(f"Saved {self.cf.path}")

    def save_file_as(self):
        if not self.cf:
            return
        # Default the dialog name to "<current-config-name>.txt" so the file on
        # disk follows the config's identity.
        name = self._current_name()
        if self.cf.path:
            default = self.cf.path.parent / (f"{name}.txt" if name else self.cf.path.name)
        else:
            default = Path(self._dialog_start_dir()) / (f"{name}.txt" if name else "config.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save WS config as", str(default),
                                              "WS config (*.txt *.cfg);;All files (*)")
        if not path:
            return
        try:
            wc.save_file(self.cf, path)
        except Exception as e:
            QMessageBox.critical(self, "Save error", str(e))
            return
        if self.summary_page:
            self.summary_page.set_path(self.cf.path)
        self._update_title()
        self.status.showMessage(f"Saved {path}")

    # -- name / title sync -------------------------------------------------

    def _current_name(self) -> str:
        if not self.cf:
            return ""
        scn = self.cf.find("SCN")
        if scn and len(scn.values) > 1:
            return scn.values[1]
        return ""

    def _update_title(self) -> None:
        base = f"{ver.APP_NAME} {ver.VERSION}"
        name = self._current_name()
        if name:
            self.setWindowTitle(f"{base} - {name}")
        elif self.cf and self.cf.path:
            self.setWindowTitle(f"{base} - {self.cf.path.name}")
        else:
            self.setWindowTitle(base)

    def _show_about(self) -> None:
        url_line = f"<p><a href='{ver.URL}'>{ver.URL}</a></p>" if ver.URL else ""
        html = (
            f"<h3>{ver.APP_NAME} {ver.VERSION}</h3>"
            f"<p>Editor for Wakespeed WS500 / WS500-Pro configuration files.</p>"
            f"<p><b>Targets:</b> {ver.GUIDE_TITLE} v{ver.GUIDE_VERSION}<br>"
            f"Schema and field semantics in <code>ws_schema.json</code> are derived "
            f"from this guide. If your firmware or guide version differs, treat "
            f"this app as advisory and cross-check against the Wakespeed docs.</p>"
            f"<p>{ver.COPYRIGHT}<br>"
            f"License: {ver.LICENSE} (see LICENSE file).</p>"
            f"{url_line}"
        )
        QMessageBox.about(self, f"About {ver.APP_NAME}", html)

    def _dialog_start_dir(self) -> str:
        if self.cf and self.cf.path:
            return str(self.cf.path.parent)
        return str(Path.cwd())

    def _on_summary_name_edited(self, new_name: str) -> None:
        """User typed a new name on the Summary page. Propagate to $SCN and title."""
        if not self.cf:
            return
        scn = self.cf.find("SCN")
        if not scn:
            return
        # Ensure SCN has room for 3 fields (Enable Wireless, Reg Name, Password).
        while len(scn.values) < 2:
            scn.values.append("1" if not scn.values else "")
        scn.values[1] = new_name
        scn.dirty = True
        # Reflect into the SCN page's Reg Name row, if open.
        scn_page = next((p for p in self.pages if p.cmd is scn), None)
        if scn_page:
            for row in scn_page.rows:
                if row.spec.get("name") == "Reg Name":
                    row.reset_to(new_name)
                    break
            scn_page.apply_btn.setEnabled(any(r.is_dirty() for r in scn_page.rows))
        self._update_title()

    def _on_command_applied(self) -> None:
        """A CommandPage just applied edits. Refresh Summary + title and
        write the whole config to disk - 'Apply to file' is the save action."""
        if self.summary_page:
            self.summary_page.set_name_silent(self._current_name())
        self._update_title()
        # Apply = save. If no path yet, prompt for one (Save As).
        self.save_file()
        if self.preview_page:
            self.preview_page.refresh()

    def _on_header_applied(self) -> None:
        """Summary's Apply: header notes have been replaced in the cf model.
        Save the file and refresh the preview."""
        self.save_file()
        if self.preview_page:
            self.preview_page.refresh()

    # -- pages -------------------------------------------------------------

    def rebuild_pages(self):
        self.sidebar.clear()
        while self.stack.count():
            w = self.stack.widget(0); self.stack.removeWidget(w); w.deleteLater()
        self.pages = []
        self.row_to_stack = []
        self.preview_page = None

        if not self.cf:
            return

        # Summary page
        self.summary_page = SummaryPage(self.cf, self.schema_by_code)
        self.summary_page.nameEdited.connect(self._on_summary_name_edited)
        self.summary_page.headerApplied.connect(self._on_header_applied)
        self.stack.addWidget(self.summary_page)
        self.sidebar.addItem(QListWidgetItem("Summary"))
        self.row_to_stack.append(0)

        sv = self.cf.sv_multiplier

        # Bucket commands by code, preserving file order across profiles.
        by_code: dict[str, list[wc.CommandLine]] = {}
        for c in self.cf.commands:
            by_code.setdefault(c.code, []).append(c)

        # Track which codes have been placed so leftovers go into "Other".
        placed: set[str] = set(HIDDEN_CODES)

        def _add_group(title: str, codes: list[str]) -> None:
            # Only add the header if at least one of its codes is present.
            present = [code for code in codes if by_code.get(code)]
            if not present:
                return
            hdr = QListWidgetItem(title)
            f = QFont(); f.setBold(True); hdr.setFont(f)
            hdr.setFlags(Qt.NoItemFlags)            # not selectable, not enabled
            hdr.setForeground(self.palette().color(QPalette.WindowText))
            self.sidebar.addItem(hdr)
            self.row_to_stack.append(None)
            for code in present:
                for c in by_code[code]:
                    spec = self.schema_by_code.get(c.code, {
                        "code": c.code, "title": c.code, "summary": "(not in schema)",
                        "fields": [],
                    })
                    label = f"  ${c.code}" + (f":{c.profile}" if c.profile is not None else "")
                    self.sidebar.addItem(QListWidgetItem(label))
                    page = CommandPage(c, spec, sv)
                    page.global_lookup = self._global_value_lookup
                    page.dirtyChanged.connect(self._on_command_applied)
                    page.pageValueChanged.connect(self._on_any_page_changed)
                    self.row_to_stack.append(self.stack.count())
                    self.stack.addWidget(page)
                    self.pages.append(page)
                placed.add(code)

        for title, codes in SIDEBAR_GROUPS:
            _add_group(title, codes)

        # Anything in the file we didn't place (and isn't hidden) - show in Other.
        leftover = [code for code in by_code if code not in placed]
        if leftover:
            _add_group("Other", leftover)

        # Now that every page exists, refresh dependencies once more so any
        # cross-command rules can see their controller pages' live values
        # (initial __init__ refresh may have run before the target page existed).
        for p in self.pages:
            p._refresh_dependencies()

        # File preview at the bottom of the sidebar.
        sep = QListWidgetItem("")
        sep.setFlags(Qt.NoItemFlags)
        self.sidebar.addItem(sep)
        self.row_to_stack.append(None)
        self.sidebar.addItem(QListWidgetItem("File Preview"))
        self.preview_page = PreviewPage(self.cf)
        self.row_to_stack.append(self.stack.count())
        self.stack.addWidget(self.preview_page)

        self.sidebar.setCurrentRow(0)

    # -- cross-command dependency support ---------------------------------

    def _global_value_lookup(self, cmd_code: str, field_name: str) -> tuple[str | None, dict | None]:
        """Return (value_str, spec) for the named field in cmd_code, querying
        the live CommandPage if it exists, falling back to the parsed
        cf.commands values otherwise. Returns (None, None) if not found."""
        for page in self.pages:
            if page.cmd.code == cmd_code:
                row = page._row_by_name.get(field_name)
                if row is not None:
                    v, _ = row.file_value()
                    return v, row.spec
                return None, None
        # Page not yet built (init order): look up the raw cf.commands value.
        if self.cf is not None:
            cmd = self.cf.find(cmd_code)
            spec = self.schema_by_code.get(cmd_code) if cmd else None
            if cmd and spec:
                for i, fspec in enumerate(spec.get("fields", [])):
                    if fspec.get("name") == field_name:
                        if i < len(cmd.values):
                            return cmd.values[i], fspec
                        return str(fspec.get("default", "")), fspec
        return None, None

    def _on_any_page_changed(self) -> None:
        """Any row on any page changed; re-run cross-page checks everywhere."""
        for p in self.pages:
            p._refresh_dependencies()
            p._refresh_constraints()
            p._refresh_dynamic_prefixes()
            p.apply_btn.setEnabled(any(r.is_dirty() for r in p.rows))

    def on_page_change(self, idx: int):
        if 0 <= idx < len(self.row_to_stack):
            stack_idx = self.row_to_stack[idx]
            if stack_idx is not None:
                self.stack.setCurrentIndex(stack_idx)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    schema = wc.load_schema()
    app = QApplication(sys.argv)
    THEME.update(_resolve_theme())
    win = MainWindow(schema)
    win.show()
    if len(sys.argv) > 1:
        win.load(sys.argv[1])
    else:
        # No file on the command line: prompt for one. Default to the folder
        # the app itself lives in (the .exe folder for frozen builds, the
        # source folder when running from source) so the user lands where
        # they keep their configs alongside the app.
        if getattr(sys, "frozen", False):
            start_dir = Path(sys.executable).parent
        else:
            start_dir = Path(__file__).parent
        path, _ = QFileDialog.getOpenFileName(win, "Open Config File",
                                              str(start_dir),
                                              "WS config (*.txt *.cfg);;All files (*)")
        if path:
            win.load(str(Path(path)))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
