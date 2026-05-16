# Changelog

All notable changes to this project. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-05-16

Initial public release.

### Compatibility
- Targets **Wakespeed Communications and Configuration Guide v2.6.1**.
- Python 3.10+, PySide6 >= 6.6.

### Features
- Parse and edit Wakespeed WS500 / WS500-Pro config files; preserves all
  comment / blank lines, CRLF endings, and signed-zero (`-0.00`) byte-for-byte.
- Summary page: editable configuration name (mirrored to `$SCN` Reg Name,
  window title, and Save-As default), editable header notes, "Generate
  New Summary" button that lists every non-default field.
- Per-command pages grouped in the sidebar (System Config, Battery Charging);
  `$DEP` is preserved but hidden from the UI.
- Schema-driven editors per field type:
  - integer spinboxes, float spinboxes (per-field decimals),
  - checkboxes for 0/1 fields and bitmask fields (live computed value),
  - radio groups for enumerated choices (`SV_Override`, `Feature-IN`),
  - literal text editor for fields where sign-of-zero matters (`BC_Index`).
- Dependency engine: `disabled_when` with `values` / `not_values` /
  `gt` / `gte` / `lt` / `lte`; same-command and cross-command via `command`
  key; `all_of` / `any_of` compound conditions; "Active: ..." annotations
  when a row is enabled by a partial trigger.
- Cross-field constraints: `max_field` (e.g. `Alt Derate (half) < (norm)`).
- `preserve_when_disabled` for safety-critical rows that must stay editable
  even when their trigger isn't active.
- Dynamic description prefixes (`prefix_from`, `rpm_bucket`) shown in a
  distinct color to flag derived text.
- File Preview page: live rendered file content.
- Password safety: editor never echoes the file's password; empty input
  preserves the device's existing password (per the guide v2.6.1 p.74);
  Apply opens a confirmation dialog with the exact value to be written.
- Sample sanitized: no default password (`1234`) shipped.
- "Apply to file" on any page saves the whole config to disk (no separate
  Save menu).
- Theme-aware colors (dark and light); responsive layout with FlowLayout
  for bitmask / choices rows.

### Tests
- 13 headless tests covering round-trip safety (with CRLF and signed zero),
  edit / save semantics, `set_header_block` line-index shifting, dependency
  engine helpers, RPM bucket math, and header canonicalization.
