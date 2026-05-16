# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Paul (nfbr.net)
"""
Wakespeed WS500 config file parser / writer / schema model.

File format (per Wakespeed Communications and Configuration Guide v2.6.1):
  - Lines beginning with '#' or '.' are comments / sentinel.
  - Blank lines preserved.
  - Command lines look like one of:
        $XXX: <csv...>@        (system command)
        $CPx:n <csv...>@       (charge profile command, n = 7 or 8)
    The trailing '@' is required by the device; we keep it on save.
  - Voltages and amps in CPx commands are 12V / 500Ah normalized in the file.
    UI scales for display only; storage is always in normalized form.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _resource_dir() -> Path:
    """Where bundled data lives. Handles PyInstaller frozen builds."""
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile unpacks data to sys._MEIPASS; --onedir uses exe dir.
        base = getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent
        return Path(base)
    return Path(__file__).parent


SCHEMA_PATH = _resource_dir() / "ws_schema.json"

# Match a command line. Captures: code (3 letters), optional profile n, body, trailing '@'.
# Examples matched:
#   $CCN: 1,1,70,1,...,0@
#   $CPA:8 14.0,30,0,0@
#   $CPA:810.4,0,20,0@        (no space after n)
#   $DEP: AREG2.2.2@
_CMD_RE = re.compile(
    r"""^\s*
        \$(?P<code>[A-Z]{3})           # 3-letter command code
        :                              # colon separator
        (?P<prof>\d+)?                 # optional profile number
        \s*                            # optional whitespace
        (?P<body>[^@]*)                # body up to '@'
        (?P<at>@)?                     # optional terminator
        \s*$
    """,
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def schema_by_code(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {cmd["code"]: cmd for cmd in schema["commands"]}


# ---------------------------------------------------------------------------
# Parsed file model
# ---------------------------------------------------------------------------


@dataclass
class CommandLine:
    """One parsed $XXX: line. Holds raw + parsed values + original index."""

    code: str                       # e.g. "CCN"
    profile: int | None             # e.g. 8 for CPA:8, else None
    values: list[str]               # one string per CSV field, trimmed
    raw_original: str               # original line as read (no trailing newline)
    line_index: int                 # 0-based index in source file
    dirty: bool = False

    def render(self) -> str:
        """Render back to file format. Always uses canonical spacing."""
        body = ",".join(self.values)
        if self.profile is not None:
            return f"${self.code}:{self.profile} {body}@"
        return f"${self.code}: {body}@"


@dataclass
class ConfigFile:
    """A parsed config file. Preserves all non-command lines verbatim."""

    path: Path | None = None
    lines: list[str] = field(default_factory=list)          # original lines, no trailing \n
    commands: list[CommandLine] = field(default_factory=list)
    newline: str = "\n"                                     # detected line ending: "\n" or "\r\n"
    had_trailing_newline: bool = True                       # whether source ended with a newline

    # cached system-voltage multiplier from $SCO field 3 (SV_Override).
    # 0.0 in the file means "auto" - we treat that as 1.0 for display.
    @property
    def sv_multiplier(self) -> float:
        for c in self.commands:
            if c.code == "SCO" and len(c.values) >= 3:
                try:
                    v = float(c.values[2])
                    return v if v > 0 else 1.0
                except ValueError:
                    return 1.0
        return 1.0

    def commands_by_key(self) -> dict[tuple[str, int | None], CommandLine]:
        return {(c.code, c.profile): c for c in self.commands}

    def find(self, code: str, profile: int | None = None) -> CommandLine | None:
        for c in self.commands:
            if c.code == code and c.profile == profile:
                return c
        return None

    def header_comments(self) -> list[str]:
        """Top contiguous block of comment / blank lines."""
        out: list[str] = []
        for ln in self.lines:
            stripped = ln.lstrip()
            if stripped.startswith("#") or stripped.startswith(".") or stripped == "":
                out.append(ln)
            else:
                break
        return out

    def set_header_block(self, text: str) -> None:
        """Replace the top contiguous block of comment / blank lines with the
        supplied text (split on newlines). Adjusts each CommandLine's
        line_index by the delta so subsequent renders still find them at the
        right positions in cf.lines."""
        new_lines = text.splitlines()
        old_count = len(self.header_comments())
        delta = len(new_lines) - old_count
        self.lines = new_lines + self.lines[old_count:]
        if delta:
            for c in self.commands:
                c.line_index += delta


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_file(path: str | Path) -> ConfigFile:
    p = Path(path)
    with open(p, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    return parse_text(raw, path=p)


def parse_text(text: str, path: Path | None = None) -> ConfigFile:
    # Preserve raw lines without their terminators so we can rejoin with original style.
    newline = "\r\n" if "\r\n" in text else "\n"
    had_trailing = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    cf = ConfigFile(path=path, lines=lines, newline=newline, had_trailing_newline=had_trailing)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("."):
            continue
        if not stripped.startswith("$"):
            continue
        m = _CMD_RE.match(stripped)
        if not m:
            continue
        body = m.group("body").strip()
        # rstrip trailing comma garbage
        body = body.rstrip(",").strip()
        values = [v.strip() for v in body.split(",")] if body else []
        prof = int(m.group("prof")) if m.group("prof") else None
        cf.commands.append(
            CommandLine(
                code=m.group("code"),
                profile=prof,
                values=values,
                raw_original=line,
                line_index=i,
            )
        )
    return cf


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def render_file(cf: ConfigFile) -> str:
    """
    Reconstruct file text. For dirty command lines we re-render; for clean
    command lines and non-command lines we keep the original verbatim.
    """
    by_index = {c.line_index: c for c in cf.commands}
    out_lines: list[str] = []
    for i, original in enumerate(cf.lines):
        c = by_index.get(i)
        if c is not None and c.dirty:
            out_lines.append(c.render())
        else:
            out_lines.append(original)
    text = cf.newline.join(out_lines)
    if out_lines and cf.had_trailing_newline:
        text += cf.newline
    return text


def save_file(cf: ConfigFile, path: str | Path | None = None) -> Path:
    target = Path(path) if path else cf.path
    if target is None:
        raise ValueError("No path to save to.")
    text = render_file(cf)
    # newline="" disables Python's newline translation; cf.newline is already in `text`.
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    cf.path = target
    # Sync cf.lines so a subsequent save (with no further edits) still reflects
    # the just-written content rather than the original parse.
    for c in cf.commands:
        if c.dirty:
            rendered = c.render()
            cf.lines[c.line_index] = rendered
            c.raw_original = rendered
            c.dirty = False
    return target


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate(value_str: str, field_spec: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, error_msg). Empty/whitespace is treated as invalid for numerics."""
    t = field_spec.get("type", "string")
    if t == "string" or t == "csv_strings":
        max_len = field_spec.get("max_len")
        if max_len is not None and len(value_str) > max_len:
            return False, f"max length {max_len}"
        if t == "string":
            if any(ch in value_str for ch in (" ", ",", "@")):
                return False, "no spaces, commas, or '@'"
        return True, ""
    if value_str.strip() == "":
        return False, "required"
    try:
        if t == "int":
            v: float = int(value_str)
        else:
            v = float(value_str)
    except ValueError:
        return False, f"not a {t}"
    lo = field_spec.get("min")
    hi = field_spec.get("max")
    if lo is not None and v < lo:
        return False, f"< min ({lo})"
    if hi is not None and v > hi:
        return False, f"> max ({hi})"
    return True, ""


def display_to_file(value_str: str, field_spec: dict[str, Any], sv: float) -> str:
    """
    Convert a UI-entered value to file form.
    For scale='V' fields, UI value is at system voltage; divide by sv.
    For scale='A' fields, we currently do not auto-scale by battery capacity
    (BC multiplier is not always known and the user spec was specifically
    voltage). Pass-through.
    """
    scale = field_spec.get("scale")
    if scale != "V" or not value_str.strip():
        return value_str
    try:
        v = float(value_str)
    except ValueError:
        return value_str
    file_v = v / sv if sv else v
    # keep 2 decimals like guide samples
    return f"{file_v:.2f}"


def file_to_display(value_str: str, field_spec: dict[str, Any], sv: float) -> str:
    scale = field_spec.get("scale")
    if scale != "V" or not value_str.strip():
        return value_str
    try:
        v = float(value_str)
    except ValueError:
        return value_str
    disp = v * sv
    return f"{disp:.2f}"


# ---------------------------------------------------------------------------
# Dependency / engine helpers (Qt-free so they can be tested independently)
# ---------------------------------------------------------------------------


def values_equal(a: str, b: Any, spec: dict[str, Any]) -> bool:
    """Compare a string from a row's file_value to a JSON literal under the
    controlling field's type. Float comparison tolerates 0 == 0.0 etc."""
    t = spec.get("type")
    if t == "int":
        try:
            return int(a) == int(b)
        except (ValueError, TypeError):
            return str(a) == str(b)
    if t == "float":
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (ValueError, TypeError):
            return str(a) == str(b)
    return str(a) == str(b)


def compare_numeric(val: str, cond: dict[str, Any]) -> bool:
    """Evaluate gt/gte/lt/lte operators in a dependency condition. Multiple
    operators in one condition are AND-ed together. Returns False if `val`
    isn't parseable as a number."""
    try:
        v = float(val)
    except (ValueError, TypeError):
        return False
    if "gt" in cond and not (v > float(cond["gt"])):
        return False
    if "gte" in cond and not (v >= float(cond["gte"])):
        return False
    if "lt" in cond and not (v < float(cond["lt"])):
        return False
    if "lte" in cond and not (v <= float(cond["lte"])):
        return False
    return True


def rpm_bucket_prefix(rfm_rpm_str: str, index: int) -> str:
    """Compute the 'RPM start-end:' label for RFM bucket `index` (1..8) from
    the controller's RFM_RPM value. The guide rounds RFM_RPM down to the
    nearest 100 before bucketing; bucket 8 has no upper limit. Returns ''
    when RFM_RPM is 0 (buckets disabled) or unparseable."""
    try:
        rfm = abs(int(float(rfm_rpm_str)))
    except (ValueError, TypeError):
        return ""
    if rfm == 0:
        return ""
    rfm_used = (rfm // 100) * 100
    range_div = rfm_used / 8.0
    start = int((index - 1) * range_div)
    end = int(index * range_div)
    if index >= 8:
        return f"RPM {start}+ : "
    return f"RPM {start}-{end}: "


def canonicalize_header(text: str) -> str:
    """Sanitize a header notes block so every line starts with '#' (comment)
    or '.' (sentinel). Empty lines become '#'. Other lines get '# ' prepended.
    Used by the Summary page's Apply before calling set_header_block."""
    out: list[str] = []
    for ln in text.split("\n"):
        s = ln.lstrip()
        if s == "":
            out.append("#")
        elif s.startswith("#") or s.startswith("."):
            out.append(ln)
        else:
            out.append("# " + ln)
    return "\n".join(out)
