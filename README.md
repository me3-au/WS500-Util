# WS500 Util

**Version 1.0.2** · Editor for Wakespeed WS500 / WS500-Pro configuration text files.

More detailed than the mobile app, it lets you view and edit Wakespeed config `.txt` files more easily.

Schema and field semantics target **Wakespeed Communications and Configuration Guide v2.6.1**. If your firmware or guide version differs, treat this app as advisory and cross-check against Wakespeed's docs.

Licensed under **GPL-3.0-or-later** — see [LICENSE](LICENSE). Release history: [CHANGELOG.md](CHANGELOG.md).

---

## Download

Prebuilt Windows binaries are on the **[Releases page](https://github.com/me3-au/WS500-Util/releases)**. Download the latest `WS500Util-vX.Y.Z-windows.zip`, unzip anywhere, and run `WS500Util.exe`. No install, no Python required.

> **First launch:** Windows SmartScreen may show "Windows protected your PC" because the .exe isn't yet signed. Click *More info* → *Run anyway*. The app is open source — review the code here or build it yourself from source (see [Dev Info](#dev-info)).

---

## What it does

For users editing a Wakespeed config file:

- **Open a config** — on launch, pick a `.txt` config (defaults to the folder the app lives in). Comments, blank lines, and line endings (including CRLF) are preserved.
- **Summary page** — edit the configuration name (also used as the window title and Save-As default filename), edit header notes, and see the system voltage multiplier. "Generate New Summary" rebuilds the notes block listing every non-default field.
- **Per-command editor pages** — sidebar-grouped under System Config and Battery Charging. Each row shows Field, Current, New, Valid Range, Default, and Notes. Voltage fields show both the system-target value and the file-normalized (12V) value.
- **Smart editors** — spinboxes, checkboxes, radio groups for enumerated choices, and bitmask checkbox rows (e.g. Required/Ignore Sensors) with a live computed value.
- **Dependent fields** — related options grey out when they don't apply (e.g. RFM table entries when RFM is off). Some safety-critical fields stay editable even when their trigger isn't active.
- **Live constraints** — Alt Derate (half) and Alt Derate (small) must stay below Alt Derate (norm); enforced while editing and on Apply.
- **File Preview** — bottom sidebar page showing the file content as it would be written to disk.
- **Apply to file** — on any page, commits edits and saves the whole config (prompts Save As only when no path is set yet). Unedited lines stay byte-identical.
- **Password safety** — the Reg Password field never shows the file's password; leave it empty to leave the device password unchanged. Apply asks for confirmation before writing a password change.

### Known limits

- Amp fields are shown and edited in file units (relative to a 500Ah battery). The regulator applies the battery-capacity multiplier at runtime; this app does not auto-scale them.
- `$DEP` is kept and saved verbatim but not shown in the sidebar.
- No diff-before-save dialog, and no undo/redo beyond per-row Apply.
- Changing `$SCO` SV_Override mid-session does not rebuild voltage editors on other pages — reopen the file to refresh.
- Older `$SCO` lines with fewer fields are tolerated on read; the first Apply may append missing trailing fields at their defaults.

---

## Dev Info

Implementation conventions, schema field keys, dependency rules, and full PyInstaller build notes live in [CLAUDE.md](CLAUDE.md).

### Requirements

- **Python 3.10+** (uses runtime `X | None` annotations).
- `PySide6 >= 6.6`.

### Setup (Windows)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python ws500_util.py
```

### Setup (macOS)

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python ws500_util.py
```

### Tests

```
python test_roundtrip.py
```

Headless tests covering: parsing, byte-identical no-edit round-trip, single-field edit produces a single-line diff, voltage display/save conversion, validation rejection, save-twice idempotency, CRLF round-trip, `set_header_block` line-index shifting, type-aware value comparison, `gt`/`gte`/`lt`/`lte` operators, RPM bucket prefix derivation, and header canonicalization.

### Packaging

See [CLAUDE.md](CLAUDE.md) for the full PyInstaller build commands. Default is `--onedir`; `--onefile` is also documented. `ws_schema.json` is bundled via `--add-data`. Version bumps must keep `_version.py`, `version_info.txt`, and this README's version banner in lockstep — see the Versioning + release section in CLAUDE.md.
