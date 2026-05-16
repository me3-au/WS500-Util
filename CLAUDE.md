# CLAUDE.md - WS500 Util

Editor for Wakespeed WS500 / WS500-Pro config files (`$XXX:` / `$CPx:n` lines, per *Wakespeed Communications and Configuration Guide v2.6.1*).

## Versioning + release

- **Source of truth:** `_version.py` (`APP_NAME`, `VERSION`, `AUTHOR`, `COPYRIGHT`, `LICENSE`, `GUIDE_VERSION`, `GUIDE_TITLE`, `URL`). Everything that displays version info — window title, About dialog, Summary compatibility line, README, CHANGELOG — should read from here.
- **Bump procedure for a release**:
  1. Edit `_version.py` -> update `VERSION` (follow SemVer: major for breaking changes, minor for additions, patch for fixes).
  2. If `ws_schema.json` was updated against a newer Wakespeed guide, also update `GUIDE_VERSION` here and in the schema's `_meta.guide` field.
  3. Add a `## [X.Y.Z] - YYYY-MM-DD` section at the top of `CHANGELOG.md` summarizing the changes.
  4. Run `python test_roundtrip.py` — all tests must pass.
  5. Rebuild the distributable (see "Build a standalone Windows .exe" below).
  6. Commit with `Release vX.Y.Z` and tag: `git tag vX.Y.Z`.
- **Guide / firmware drift:** when Wakespeed publishes a new guide, audit `ws_schema.json` field-by-field against the new version. Bump `GUIDE_VERSION` once the schema has been validated against the new doc.

## Project layout

- `ws500_util.py` - PySide6 GUI entry point.
- `ws_config.py` - parser, writer, schema model, plus Qt-free engine helpers (`values_equal`, `compare_numeric`, `rpm_bucket_prefix`, `canonicalize_header`). No Qt dependency. Loads `ws_schema.json` via `_resource_dir()` which already handles PyInstaller frozen builds (`sys._MEIPASS`).
- `ws_schema.json` - field metadata for all 13 commands. Fields marked `"scale": "V"` are 12V-normalized in the file and shown at system voltage in the UI (multiplier from `$SCO` field 3). See the `_meta` block for the full schema key reference.
- `test_roundtrip.py` - headless tests; do not import PySide6. Run before any build.
- `samples/` - example configs (`ALT_APS160v14.txt`).

**Python 3.10+** required (uses `X | None` runtime annotations).

## Invariants - don't break these

1. **Round-trip safety.** Unedited lines, comments, blank lines, and the `.` sentinel on line 12 of the sample must be byte-identical on save. Tested by `test_roundtrip_no_edits`.
2. **`ws_schema.json` must ship with the exe.** It is read at startup. Build commands below pass `--add-data`.
3. **Voltage scaling lives in `ws_config.display_to_file` / `file_to_display`** (and is also implemented by the `scale=='V'` path inside `FieldRow._make_editor` / `file_value` for live spinbox I/O). Do not duplicate conversion logic elsewhere.
4. **Validation is against file-units**, not display-units. For voltage spinboxes the displayed range is `min*sv .. max*sv`; the saved string is `editor_value / sv` formatted with `decimals`.
5. **Password field is sensitive.** Reg Password on `$SCN` starts empty regardless of file content (empty = "leave existing device password unchanged" per guide v2.6.1 p.74). Page-load + Apply does NOT silently rewrite the device password; a value is only written if the user explicitly typed one. Any password change opens a confirmation dialog.
6. **Sample must not ship a real password.** `samples/ALT_APS160v14.txt` `$SCN` line ends with `,@` (empty password slot). Do NOT restore `1234` or any other literal value.
7. **CRLF preserved.** `ConfigFile.newline` is detected at parse and used by `render_file` / `save_file` (`newline=""` to suppress Python's translation). CRLF input round-trips byte-identical (`test_roundtrip_crlf`).

## Schema field conventions (`ws_schema.json`)

Per-field keys understood by the parser/UI:

- `name` (required), `type` (`int` | `float` | `string` | `csv_strings`)
- `min` / `max` — file-units bounds. Shown in the "Valid Range" column. The spinbox **widens** its range to accept any value already in the file so we never silently clamp on load.
- `default` — used when a row is disabled by dependency, and shown in the "Default" column.
- `desc` — supports inline HTML (rendered with `Qt.RichText`). Use for emphasis: `<b style='color:#c00'>CRITICAL FOR MANY 48V ALTS</b>`, `<i>Note: Legacy.</i>`.
- `decimals` — float fields only. Editor precision and save formatting. Defaults to 2; current overrides:
  - `Eng/Alt Drive Ratio`: 3 (sample shows `3.100`)
  - `VBat Comp per 1C`: 3 (range 0.0..0.1)
- `scale` — `"V"` (12V-normalized in file, display = file × sv) or `"A"` (500Ah-normalized; not auto-scaled, see README "Known limits").
- `direction` — `"Tx"` | `"Rx"` | `"Rx/Tx"` | `"Internal"`. Renders a colored badge before the description so the user can see how the field interacts with the CAN bus (transmit, receive, both, or regulator-internal logic). Currently tagged on every `$CCN` field. If you want to tag other commands, just add the key — no code change needed.
- `sensitive` — `"password"`. Triggers masked editor, empty default, warnings, Apply confirmation.
- `bits` — list of `{value, label}` objects. Renders the field as a row of checkboxes; the integer value is recomputed as the OR of checked bits + any unknown bits already in the file (those are preserved on save). Used by `$SCA` `Required Sensors` and `Ignore Sensors`.
- `as` — editor override:
  - `"text"`: literal-preserving `QLineEdit` instead of a spinbox. Used by `$SCO` `BC_Index` because `QDoubleSpinBox` can't represent signed zero.
  - `"choices"`: render a row of `QRadioButton`s, one per entry in the `choices` array. Each radio is labelled `<label>  (<file value>)` so the user can see what the file value will be. Unmatched file values leave all radios unchecked and the literal source value is preserved. Used by `$SCO` `SV_Override` and `$SCO` `Feature-IN`.
- `choices` — list of `{value, label}` pairs. Required when `as == "choices"`.
- `disabled_when` — see below.

## Editor kinds (`FieldRow._resolve_kind`)

The UI picks an editor widget per field, dispatched on `_kind`:

| `_kind`    | Trigger                                  | Widget                                  |
| ---------- | ---------------------------------------- | --------------------------------------- |
| `password` | `sensitive == "password"`                | `QLineEdit` (`Password` echo mode)      |
| `text`     | `as == "text"`                           | `QLineEdit` with manual float range validation; preserves the literal source string (used by `BC_Index` for signed zero) |
| `choices`  | `as == "choices"` (and spec has `choices`) | row of `QRadioButton` widgets, one per choice. Label shows `<name>  (<file value>)`. |
| `dropdown` | `as == "dropdown"` (and spec has `choices`) | `QComboBox`. Item text `<value> - <label>`; file value stored as itemData. Unknown values preserved as `(custom: N)`. |
| `bitmask`  | spec has `bits`                          | row of `QCheckBox` widgets              |
| `checkbox` | `type=int` AND `min=0` AND `max=1`       | `QCheckBox` (no label - the row's name column carries it) |
| `int`      | `type=int`                               | `QSpinBox`                              |
| `float`    | `type=float`                             | `QDoubleSpinBox`                        |
| `string`   | `type=string` or `csv_strings`           | `QLineEdit`                             |

Untouched-value preservation: for every kind except `string`, `file_value()` returns the literal `_file_value` until the user actually interacts with the editor. This protects against e.g. `QDoubleSpinBox` silently normalizing `-0.00` to `0.00` and Apply losing the sign.

## Dependency mapping (`disabled_when`)

A row with a `disabled_when` clause is **greyed out** and **snapped to its schema default** when the condition holds. On Apply, disabled rows save as the default — this keeps stale "irrelevant" values from sitting in the config. The condition supports three forms:

1. **Simple, same-command**:
   ```json
   "disabled_when": {"field": "<name>", "values": [v1, v2, ...]}
   ```
2. **All-of (compound, disabled when EVERY condition matches)**:
   ```json
   "disabled_when": {
     "all_of": [
       {"command": "<code>", "field": "<name>", "values": [...], "active_label": "..."},
       ...
     ]
   }
   ```
3. **Any-of (disabled when ANY condition matches)** — same shape as `all_of`.

Each condition supports:
- `command` — defaults to the row's own command. Use to point at a field in another command.
- `field` — controller field name within that command.
- `values` — controller is "in this set" → condition matches.
- `not_values` — controller is "NOT in this set" → condition matches. (Use to express e.g. "RFM_RPM != 0".)
- `gt` / `gte` / `lt` / `lte` — numeric comparison. Condition matches when the controller's value satisfies the operator. Use when range membership is impractical to list (e.g. "RFM_RPM > 0"). Multiple ops in one condition are AND-ed.
- `active_label` — friendly name shown in the row's Notes column when the row is **enabled** because this condition didn't match. The engine collects all "active triggers" and renders e.g. `Active: Trigger Half-Power RPM=1500, Feature-In=2`.

Value comparison (`wc.values_equal`, exposed from `ws_config.py`) is type-aware:
- Controller type `int`: integer equality (`"0"` matches `0`).
- Controller type `float`: `|a - b| < 1e-9` (`"0"`, `"0.0"`, `"0.00"` all match `0` / `0.0`).
- Otherwise: string equality.

The numeric comparison helpers (`wc.values_equal`, `wc.compare_numeric`, `wc.rpm_bucket_prefix`, `wc.canonicalize_header`) live in `ws_config.py` so they can be tested without instantiating Qt widgets. See `test_roundtrip.py`.

### Cross-command plumbing

`CommandPage._refresh_dependencies` resolves the controller value via `_lookup_value_and_spec(cmd_code, field_name)`. Same-command lookups hit `_row_by_name` directly. Cross-command lookups go through `CommandPage.global_lookup`, which `MainWindow` sets to `_global_value_lookup` — that walks `self.pages` first (live editor values) and falls back to `cf.commands` when the controller's page hasn't been built yet (init order).

Whenever any row changes, `CommandPage.pageValueChanged` fires; `MainWindow._on_any_page_changed` then re-runs `_refresh_dependencies` on **every** page so cross-command rules see the new value immediately.

### Current dependency map

| Command | Controller(s)                                       | Disable when             | Dependent fields                               |
| ------- | --------------------------------------------------- | ------------------------ | ---------------------------------------------- |
| CNG     | `RFM_RPM`                                           | `== 0`                   | `RFM1` .. `RFM8`                               |
| CPO     | `Limit Amps`                                        | `== 0`                   | `Exit Duration`, `Exit VBat`, `Exit Amps`      |
| CPE     | `VBat Set Point`                                    | `== 0 / 0.0`             | `Max Amps`, `Exit Duration`, `Exit Amps`       |
| CPB     | `VBat Comp per 1C`                                  | `== 0 / 0.0`             | `Min Comp Temp`                                |
| CPB     | `Rdc Volts`                                         | `== 0 / 0.0`             | `Rdc Low Temp`, `Rdc High Temp`, `Rdc Amps`    |
| SCA     | `$SCT.Trigger Half-Power RPM` AND `$SCO.Feature-IN` | `==0` AND `!=2` (all_of) | `Alt Derate (half)` (cross-command)            |
| SCA     | `$CNG.RFM_RPM`                                      | `!= 0` (any_of)          | `PBF` (either/or with White Space)             |
| CNG     | `$SCA.PBF`                                          | `!= 0` (any_of)          | `RFM_RPM` (either/or with PBF)                 |
| SCO     | `$CNG.RFM_RPM`                                      | `> 0` (any_of)           | `Feature-IN` (repurposed to gate RFM; $SCO setting ignored by the device) |

When adding a new dependency:

1. Add a `disabled_when` clause to the dependent field in `ws_schema.json`. Use `command` to point at another command's field.
2. Update the table above.
3. No code change needed — the engine, broadcast, and global lookup already handle cross-command rules.

## UI conventions

- Sidebar groups: `SIDEBAR_GROUPS` in `ws500_util.py`. `HIDDEN_CODES` (currently `{"DEP"}`) are kept in the file and saved verbatim but never shown in the sidebar. Anything not grouped and not hidden lands in an "Other" group at the bottom. A read-only **File Preview** page is appended at the bottom of the sidebar.
- Command pages use the column layout `Field | Current | New | Valid Range | Default | Notes`. The Notes column is rendered by `FieldRow._render_note()` from priority-ordered state:
  1. transient edit feedback (validation error / password-edit warning) — `_note_edit`
  2. cross-field constraint violation — `_note_constraint`
  3. dependency-disabled annotation — `_note_disabled`
  4. cross-command "Active: ..." annotation — `_note_info`
  5. password baseline ("File has ..." status)
  Each state field is set by its dedicated method (e.g. `set_constraint_error`, `set_info_text`) which then calls `_render_note`. Higher-priority messages temporarily mask lower-priority ones; when they clear, the lower message reappears.
- Field descriptions are rendered as `Qt.RichText` (HTML allowed) and capped at 800px wide so long text wraps onto multiple lines instead of stretching across wide windows.
- The Summary page hosts the canonical editable **Configuration name**, mirrored to `$SCN` Reg Name, the window title, and the Save As default filename. It also holds an editable header notes block (canonicalized via `wc.canonicalize_header` on Apply — every line must start with `#` or `.`) and a "Generate New Summary" button that writes a fresh notes block listing every non-default field.
- **Apply = Save.** "Apply to file" on a command page or the Summary page writes the whole config to disk (prompts Save As only when no path is set). There is no separate Save menu.
- File menu has only Quit. On app launch, a file-open dialog appears defaulting to `samples/`.
- Theme: `_resolve_theme()` picks readable color sets for light vs dark by inspecting `QPalette.Window` lightness at app start. Cross-command-sourced text (dynamic prefixes, bitmask computed value) uses the `dynamic` color so users can distinguish derived text from fixed schema text.

## Build a standalone Windows .exe with PyInstaller

Default to `--onedir`. It starts faster, is easier to debug, and is less likely to be flagged by Defender. Switch to `--onefile` only if a single-file distribution is required.

### One-time setup (in the project folder)

```cmd
cd "C:\Users\adren\AppDev\Wakespeed\WS500 Util"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

### Test before building

```cmd
python test_roundtrip.py
```

All tests must pass. If any fail, stop and fix the parser/writer/schema before building.

### Build (onedir - default, recommended)

```cmd
pyinstaller --noconfirm --clean --windowed --name WS500Util ^
  --add-data "ws_schema.json;." ^
  --add-data "samples;samples" ^
  ws500_util.py
```

Output: `dist\WS500Util\WS500Util.exe` plus a folder of dependencies. Ship the whole folder (zip it).

### Build (onefile - single .exe)

```cmd
pyinstaller --noconfirm --clean --windowed --onefile --name WS500Util ^
  --add-data "ws_schema.json;." ^
  --add-data "samples;samples" ^
  ws500_util.py
```

Output: `dist\WS500Util.exe`. First-run cold start is ~3-5s while it unpacks to `%TEMP%`. Defender SmartScreen may complain - users have to click "More info -> Run anyway" until the binary is signed.

### Verify the build

1. Open `dist\WS500Util\WS500Util.exe` (or `dist\WS500Util.exe`).
2. File -> Open `samples\ALT_APS160v14.txt`.
3. Confirm sidebar shows Summary + 13 command pages.
4. Confirm Summary shows "System voltage multiplier (from $SCO): x4".
5. Open `$CPA:8`, confirm VBat Set Point shows `56.00` (file value `14.0` x4).
6. Edit to `56.40`, click "Apply to file", File -> Save As to a temp path, reopen, confirm the value persisted as `14.10` in the raw line.

If step 4 or 5 fails, the schema or data file isn't being bundled - check that `ws_schema.json` is next to `WS500Util.exe` in onedir, or that `--add-data "ws_schema.json;."` was passed for onefile. The semicolon is required on Windows (Mac/Linux use `:`).

### Icon (optional)

Add `--icon=ws500.ico` once an icon file exists. Skip if no icon.

### Code signing (optional, defers Defender warnings)

Requires an Authenticode certificate. Out of scope for this doc - sign `dist\WS500Util.exe` with `signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 WS500Util.exe`.

## Build for macOS (later)

Same flow on a Mac, but `--add-data` uses `:` not `;`:

```bash
pyinstaller --noconfirm --clean --windowed --name WS500Util \
  --add-data "ws_schema.json:." \
  --add-data "samples:samples" \
  ws500_util.py
```

Output: `dist/WS500Util.app`. Notarization is a separate step.

## Common breakage

- **`FileNotFoundError: ws_schema.json`** in the built exe -> `--add-data` was missing or used the wrong separator (`;` on Windows, `:` on Mac/Linux).
- **Blank window / immediate exit** -> almost always a missing Qt plugin. Rebuild with `--clean` and check the console (drop `--windowed` temporarily so stderr is visible).
- **`ModuleNotFoundError: PySide6.QtCore`** at runtime -> built from a venv where PySide6 wasn't installed. Re-run `pip install -r requirements.txt` inside the active venv.
- **Defender quarantines the exe** -> known PyInstaller issue. Either sign the binary or distribute via a trusted channel.

## When adding a new $XXX command or field

1. Add the field to `ws_schema.json` under the correct command's `fields[]`. Include `name`, `type`, `min`/`max` (or `max_len`), `default`, `desc`, and `"scale": "V"` if it's a voltage.
2. Run `python test_roundtrip.py`. If the sample file's command for that line now has fewer values than the schema, `CommandPage` pads from the field default - confirm the rendered line still parses.
3. Rebuild the exe.

## Out of scope (don't add without asking)

- Auto-update / online schema fetch.
- Sending commands to a live regulator over serial / Bluetooth.
- Diff-before-save UI (mentioned in README "Known limits").
- Amp-field battery-capacity scaling (deliberately deferred - see README).
