# WS500 Util

**Version 1.0.1** · Editor for Wakespeed WS500 / WS500-Pro configuration text files.

Schema and field semantics target **Wakespeed Communications and Configuration Guide v2.6.1**. If your firmware or guide version differs, treat this app as advisory and cross-check against Wakespeed's docs. See [CHANGELOG.md](CHANGELOG.md) for release history and [CLAUDE.md](CLAUDE.md) for implementation conventions.

Licensed under **GPL-3.0-or-later** — see [LICENSE](LICENSE).

## What it does

- Parses a config `.txt`; preserves all comment / header lines verbatim (including CRLF if the source used it).
- On launch, prompts for a config file (defaults to the `samples/` folder).
- **Summary** page — editable configuration name (mirrored to `$SCN` Reg Name, window title, and Save-As default filename), editable header notes (with a "Generate New Summary" button that lists every non-default field), and the system voltage multiplier.
- Per-command editor pages, sidebar-grouped (System Config, Battery Charging). Each row shows `Field | Current | New | Valid Range | Default | Notes`. Voltage fields show both system-target and file-normalized values.
- Smart input widgets driven by the schema: integer spinboxes, float spinboxes with per-field decimals, checkboxes for 0/1 fields, radio groups for enumerated choices (`$SCO` SV_Override, Feature-IN), and bitmask checkbox rows with a live computed value (`$SCA` Required/Ignore Sensors).
- **Dependency engine.** Fields with `disabled_when` (simple, `all_of`, `any_of`, with `values`/`not_values`/`gt`/`gte`/`lt`/`lte` operators) grey out when their controller matches. Cross-command rules are supported. Some safety-critical fields (Alt Derate (half)) stay editable when disabled.
- **Cross-field constraints.** `Alt Derate (half)` and `Alt Derate (small)` must be less than `Alt Derate (norm)` — enforced live and on Apply.
- **File Preview** — bottom-of-sidebar page showing the file content as it would be written to disk.
- "Apply to file" on any page commits row edits and saves the whole config to disk (prompts Save As only when no path is set yet).
- Round-trip safe: unedited lines, comments, blank lines, CRLF endings, signed zero (`-0.00`) all preserved.
- Password safety: the Reg Password editor never echoes the file's password back; empty = "leave device password unchanged" (per the v2.6.1 guide); Apply opens a confirmation dialog before writing any password change.

## Requirements

- **Python 3.10+** (uses runtime `X | None` annotations).
- `PySide6 >= 6.6`.

## Setup (Windows)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python ws500_util.py
```

## Setup (macOS)

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python ws500_util.py
```

## Tests

```
python test_roundtrip.py
```

Headless tests covering: parsing, byte-identical no-edit round-trip, single-field edit produces a single-line diff, voltage display/save conversion, validation rejection, save-twice idempotency, CRLF round-trip, `set_header_block` line-index shifting, type-aware value comparison, `gt`/`gte`/`lt`/`lte` operators, RPM bucket prefix derivation, header canonicalization.

## Packaging

See [CLAUDE.md](CLAUDE.md) for the full PyInstaller build commands. Default is `--onedir`; `--onefile` is also documented. `ws_schema.json` is bundled via `--add-data`.

## Known limits / not-yet-done

- Amp fields (`scale: "A"`) are *not* auto-scaled by battery capacity. The file stores values relative to a 500Ah battery; the regulator applies the BC multiplier at runtime. Amp values are shown and edited in file-units.
- `$DEP` is treated as a single CSV string field; not displayed in the sidebar (kept and saved verbatim).
- No diff-before-save dialog.
- No undo/redo beyond the per-row Apply.
- Sample file `$SCO` has 6 fields; schema expects 7 (`Promiscuous Mode`). Missing trailing fields are tolerated on read; first Apply on `$SCO` will append the 7th value at its default.
- Changing `$SCO SV_Override` mid-session does **not** rebuild the voltage editors on other pages — reopen the file to refresh.
