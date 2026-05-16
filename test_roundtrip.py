# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Paul (nfbr.net)
"""
Headless tests for parser/writer. Does not import PySide6.
Run:  python test_roundtrip.py
"""

from __future__ import annotations
import sys
import tempfile
from pathlib import Path

import ws_config as wc


HERE = Path(__file__).parent
SAMPLE = HERE / "samples" / "ALT_APS160v14.txt"


def test_parse_sample():
    cf = wc.parse_file(SAMPLE)
    codes = [(c.code, c.profile) for c in cf.commands]
    expected = [
        ("CCN", None), ("SCA", None), ("SCT", None), ("SCN", None),
        ("SCO", None), ("CNG", None), ("DEP", None),
        ("CPA", 8), ("CPO", 8), ("CPF", 8), ("CPP", 8), ("CPE", 8), ("CPB", 8),
    ]
    assert codes == expected, f"got {codes}"
    print(f"[OK] parsed {len(cf.commands)} commands")
    print(f"     SV multiplier = x{cf.sv_multiplier:g}")
    # The sample has SCO field 3 = 4.00  -> 48V system
    assert cf.sv_multiplier == 4.0, cf.sv_multiplier
    print(f"[OK] sv_multiplier == 4.0 (48V system)")


def test_roundtrip_no_edits():
    """Save without edits. Non-command lines must be byte-identical."""
    cf = wc.parse_file(SAMPLE)
    text = wc.render_file(cf)
    original = SAMPLE.read_text(encoding="utf-8")
    # Compare line by line. Non-command lines must match exactly.
    orig_lines = original.splitlines()
    new_lines = text.splitlines()
    assert len(orig_lines) == len(new_lines), f"{len(orig_lines)} vs {len(new_lines)}"
    diffs = 0
    for i, (a, b) in enumerate(zip(orig_lines, new_lines)):
        if a != b:
            diffs += 1
            print(f"  diff line {i}:\n    orig: {a!r}\n    new : {b!r}")
    # With no edits, dirty=False for all commands so render() is not used.
    # Expect byte-equality.
    assert diffs == 0, f"{diffs} unexpected differences"
    print("[OK] round-trip no-edit is byte-identical")


def test_edit_one_field():
    cf = wc.parse_file(SAMPLE)
    sca = cf.find("SCA")
    assert sca is not None
    # Change Alt Target Temp (field index 1) from 110 to 105
    sca.values[1] = "105"
    sca.dirty = True

    out = wc.render_file(cf)
    out_lines = out.splitlines()
    in_lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    diffs = [i for i, (a, b) in enumerate(zip(in_lines, out_lines)) if a != b]
    assert len(diffs) == 1, f"expected 1 diff, got {diffs}"
    i = diffs[0]
    print(f"[OK] 1 diff on line {i}")
    print(f"     before: {in_lines[i]}")
    print(f"     after : {out_lines[i]}")
    assert "105" in out_lines[i]


def test_voltage_conversion():
    schema = wc.load_schema()
    # CPA VBat Set Point field
    cpa_fields = next(c for c in schema["commands"] if c["code"] == "CPA")["fields"]
    vbat_spec = cpa_fields[0]
    assert vbat_spec["scale"] == "V"
    # File value 14.0 on a 48V (x4) system displays as 56.0
    disp = wc.file_to_display("14.0", vbat_spec, 4.0)
    assert disp == "56.00", disp
    print(f"[OK] file 14.0 V @ x4  -> display {disp}")
    # User enters 56.0, save as file -> 14.00
    back = wc.display_to_file("56.0", vbat_spec, 4.0)
    assert back == "14.00", back
    print(f"[OK] display 56.0 V @ x4  -> file {back}")


def test_validation():
    schema = wc.load_schema()
    cpa = next(c for c in schema["commands"] if c["code"] == "CPA")["fields"]
    vbat = cpa[0]
    assert wc.validate("14.0", vbat) == (True, "")
    assert wc.validate("99.0", vbat)[0] is False
    assert wc.validate("abc",  vbat)[0] is False
    print("[OK] validation rejects out-of-range and non-numeric")


def test_save_to_tempfile():
    cf = wc.parse_file(SAMPLE)
    # edit something
    cpa = cf.find("CPA", 8)
    cpa.values[0] = "14.10"
    cpa.dirty = True
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.txt"
        wc.save_file(cf, out)
        text = out.read_text(encoding="utf-8")
        assert "$CPA:8 14.10," in text, text
        print(f"[OK] wrote tempfile with edited CPA line")


def test_save_twice_preserves_edits():
    """Edit A, save. Edit B, save. Both edits must be on disk."""
    cf = wc.parse_file(SAMPLE)
    cpa = cf.find("CPA", 8)
    cpa.values[0] = "14.10"
    cpa.dirty = True
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.txt"
        wc.save_file(cf, out)
        # second edit, different command
        cpf = cf.find("CPF", 8)
        cpf.values[0] = "13.40"
        cpf.dirty = True
        wc.save_file(cf, out)
        text = out.read_text(encoding="utf-8")
        assert "$CPA:8 14.10," in text, f"first edit lost: {text}"
        assert "$CPF:8 13.40," in text, f"second edit missing: {text}"
        print("[OK] two sequential saves preserve both edits")

    # Also: save then immediate re-save with no further edits is idempotent.
    cf2 = wc.parse_file(SAMPLE)
    cf2.find("CPA", 8).values[0] = "14.10"
    cf2.find("CPA", 8).dirty = True
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.txt"
        wc.save_file(cf2, out)
        first = out.read_text(encoding="utf-8")
        wc.save_file(cf2, out)
        second = out.read_text(encoding="utf-8")
        assert first == second, "re-save without edits changed content"
        assert "$CPA:8 14.10," in second
        print("[OK] re-saving with no further edits is idempotent")


def test_roundtrip_crlf():
    """CRLF input must round-trip as CRLF, not get normalized to LF."""
    original = SAMPLE.read_text(encoding="utf-8")
    # Force CRLF regardless of what's on disk
    lf_only = original.replace("\r\n", "\n")
    crlf = lf_only.replace("\n", "\r\n")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "crlf.txt"
        src.write_bytes(crlf.encode("utf-8"))
        cf = wc.parse_file(src)
        assert cf.newline == "\r\n", f"detected newline = {cf.newline!r}"
        out = Path(td) / "out.txt"
        wc.save_file(cf, out)
        got = out.read_bytes()
        assert got == crlf.encode("utf-8"), "CRLF input did not round-trip byte-identical"
        print("[OK] CRLF round-trips byte-identical")


def test_set_header_block():
    """set_header_block must replace the top notes block and shift each
    CommandLine's line_index by the line-count delta."""
    cf = wc.parse_file(SAMPLE)
    first_cmd = cf.commands[0]
    orig_index = first_cmd.line_index
    orig_header_count = len(cf.header_comments())

    # Longer header
    new_header = "\n".join(["# new line 1", "# new line 2", "# new line 3"] * 4)
    new_count = len(new_header.splitlines())
    cf.set_header_block(new_header)
    delta = new_count - orig_header_count
    assert first_cmd.line_index == orig_index + delta, \
        f"line_index not shifted: {first_cmd.line_index} vs {orig_index + delta}"

    # After re-render, the (clean) command line should appear at the new index.
    rendered = wc.render_file(cf)
    out_lines = rendered.splitlines()
    assert out_lines[first_cmd.line_index].lstrip().startswith(f"${first_cmd.code}"), \
        f"command not at shifted line: {out_lines[first_cmd.line_index]!r}"
    print("[OK] set_header_block shifts line_index and keeps commands findable")

    # Shorter header
    cf2 = wc.parse_file(SAMPLE)
    first_cmd2 = cf2.commands[0]
    orig_index2 = first_cmd2.line_index
    orig_count2 = len(cf2.header_comments())
    cf2.set_header_block("# only one")
    delta2 = 1 - orig_count2
    assert first_cmd2.line_index == orig_index2 + delta2
    print("[OK] set_header_block handles shrinking too")


def test_values_equal():
    int_spec = {"type": "int"}
    float_spec = {"type": "float"}
    str_spec = {"type": "string"}
    # int: '0' equals 0, '00' equals 0
    assert wc.values_equal("0", 0, int_spec)
    assert wc.values_equal("00", 0, int_spec)
    assert not wc.values_equal("0", 1, int_spec)
    # float: '0', '0.0', '0.00' all equal 0 and 0.0
    assert wc.values_equal("0", 0, float_spec)
    assert wc.values_equal("0.0", 0, float_spec)
    assert wc.values_equal("0.00", 0.0, float_spec)
    assert not wc.values_equal("0.01", 0.0, float_spec)
    # string: literal compare
    assert wc.values_equal("foo", "foo", str_spec)
    assert not wc.values_equal("foo", "bar", str_spec)
    print("[OK] values_equal handles int/float/string type-aware comparison")


def test_compare_numeric():
    assert wc.compare_numeric("5", {"gt": 0})
    assert not wc.compare_numeric("0", {"gt": 0})
    assert wc.compare_numeric("0", {"gte": 0})
    assert wc.compare_numeric("-1", {"lt": 0})
    assert not wc.compare_numeric("0", {"lt": 0})
    assert wc.compare_numeric("0", {"lte": 0})
    # Multiple ops: AND-ed.
    assert wc.compare_numeric("5", {"gt": 0, "lt": 10})
    assert not wc.compare_numeric("15", {"gt": 0, "lt": 10})
    # Unparseable -> False.
    assert not wc.compare_numeric("foo", {"gt": 0})
    print("[OK] compare_numeric gt/gte/lt/lte")


def test_rpm_bucket_prefix():
    # RFM_RPM=3200 -> range_div=400, buckets 0-400, 400-800, ...
    assert wc.rpm_bucket_prefix("3200", 1) == "RPM 0-400: "
    assert wc.rpm_bucket_prefix("3200", 4) == "RPM 1200-1600: "
    assert wc.rpm_bucket_prefix("3200", 8) == "RPM 2800+ : "
    # RFM_RPM=0 -> empty (disabled).
    assert wc.rpm_bucket_prefix("0", 1) == ""
    # Negative RFM_RPM (always-on) - absolute value used.
    assert wc.rpm_bucket_prefix("-3200", 1) == "RPM 0-400: "
    # Round down to nearest 100.
    assert wc.rpm_bucket_prefix("3299", 1) == "RPM 0-400: "  # 3299 -> 3200
    # Unparseable.
    assert wc.rpm_bucket_prefix("foo", 1) == ""
    print("[OK] rpm_bucket_prefix")


def test_canonicalize_header():
    # Empty lines become '#'.
    assert wc.canonicalize_header("") == "#"
    assert wc.canonicalize_header("\n") == "#\n#"
    # Existing '#'/'.' lines kept verbatim.
    src = "# already commented\n. sentinel\n#"
    assert wc.canonicalize_header(src) == src
    # Other lines get '# ' prepended.
    assert wc.canonicalize_header("plain line") == "# plain line"
    assert wc.canonicalize_header("first\n# second\nthird") == \
        "# first\n# second\n# third"
    # Leading whitespace is preserved on lines that already start with '#'/'.'
    assert wc.canonicalize_header("   # indented") == "   # indented"
    print("[OK] canonicalize_header prepends '#' to bare lines")


def main():
    tests = [
        test_parse_sample,
        test_roundtrip_no_edits,
        test_edit_one_field,
        test_voltage_conversion,
        test_validation,
        test_save_to_tempfile,
        test_save_twice_preserves_edits,
        test_roundtrip_crlf,
        test_set_header_block,
        test_values_equal,
        test_compare_numeric,
        test_rpm_bucket_prefix,
        test_canonicalize_header,
    ]
    fail = 0
    for t in tests:
        print(f"\n=== {t.__name__} ===")
        try:
            t()
        except Exception as e:
            print(f"[FAIL] {e}")
            fail += 1
    print(f"\n{len(tests) - fail}/{len(tests)} passed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
