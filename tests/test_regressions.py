# Roch NVRAM -- an editor for AMI SCEWIN NVRAM exports.
# Copyright (C) 2026 Roch Studio
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bios_manager.compare_tools import compare_files, has_identity, raw_value
from bios_manager.core import (
    ParsedProfile,
    PendingChange,
    ScewinDocument,
    ValidationError,
)
from bios_manager.scewin_parser import option_line_indexes, parse_sce_file, setting_to_dict


HEADER = """// Script File Name : nvram.txt
// Created on 07/10/26 at 12:00:00
// AMISCE Utility. Ver 5.05.01.0002
HIICrc32= ABCD1234

"""

# An option row at column 0. The reader always accepted these; the writer used to
# stop at the first unindented line and leave its "*" in place.
UNINDENTED_OPTIONS = """Setup Question\t= Fan Mode
Help String\t= Fan control mode
Token\t= 0100
Offset\t= 0010
Width\t= 01
BIOS Default\t= [00]Low
Options\t=[00]Low
\t[01]Mid
*[02]High

"""

# Two option codes that share a label. Comparing displayed labels cannot tell
# these apart; comparing raw values can.
DUPLICATE_LABELS = """Setup Question\t= Fan Curve
Help String\t= Fan curve selection
Token\t= 0101
Offset\t= 0011
Width\t= 01
BIOS Default\t= [00]Auto
Options\t=*[00]Auto
\t[01]Auto
\t[02]Manual

"""

# Width 0x10, so normalize_value passes the text through instead of parsing a number.
WIDE_VALUE = """Setup Question\t= Board Label
Help String\t= Free text field
Token\t= 0102
Offset\t= 0012
Width\t= 10
BIOS Default\t= <hello>
Value\t= <hello>

"""

# Records that carry no token, offset or width at all.
WITHOUT_IDENTITY = """Setup Question\t= Real
Help String\t= has an identity
Token\t= 0100
Offset\t= 0010
Width\t= 01
BIOS Default\t= <1>
Value\t= <1>

Setup Question\t= Orphan A
Help String\t= no token, offset or width
Value\t= <5>

Setup Question\t= Orphan B
Help String\t= no token, offset or width
Value\t= <5>

"""

EURO_SIGN = chr(0x20AC)  # valid Unicode, but not representable in Latin-1


def build(root: Path, records: str) -> tuple[ParsedProfile, ScewinDocument]:
    """Write a synthetic export plus its parsed profile, and open both."""
    nvram = root / "nvram.txt"
    nvram.write_text(HEADER + records, encoding="latin-1")
    parsed = parse_sce_file(nvram, source="nvram", commented=False)
    profile_path = root / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "label": "Synthetic",
                "metadata": parsed["metadata"],
                "active_settings": [setting_to_dict(item) for item in parsed["settings"]],
            }
        ),
        encoding="utf-8",
    )
    profile = ParsedProfile.load(profile_path)
    document = ScewinDocument(nvram)
    document.verify_profile(profile)
    return profile, document


class OptionBlockTests(unittest.TestCase):
    def test_reader_and_writer_agree_on_which_rows_are_options(self):
        block = UNINDENTED_OPTIONS.splitlines()
        rows = [block[index] for index in option_line_indexes(block)]
        self.assertEqual(rows, ["Options\t=[00]Low", "\t[01]Mid", "*[02]High"])

    def test_option_block_ends_at_a_blank_line_and_at_the_next_field(self):
        block = ["Options\t=[00]Low", "\t[01]Mid", "", "\t[02]High", "Token\t= 01"]
        self.assertEqual(option_line_indexes(block), [0, 1])

    def test_writing_a_change_leaves_exactly_one_selected_option(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile, document = build(root, UNINDENTED_OPTIONS)
            setting = profile.settings[0]
            self.assertEqual(
                [option["code_hex"] for option in setting["options"]], ["00", "01", "02"]
            )

            change = document.build_change(profile, setting["export_id"], "01")
            output = document.write_modified_copy(root / "apply.txt", [change])

            selected = [
                line
                for line in output.read_text(encoding="latin-1").splitlines()
                if "*[" in line
            ]
            self.assertEqual(selected, ["\t*[01]Mid"])


class QueuedChangeTests(unittest.TestCase):
    def test_duplicate_option_labels_are_distinguished_by_raw_value(self):
        with tempfile.TemporaryDirectory() as temp:
            profile, document = build(Path(temp), DUPLICATE_LABELS)
            setting = profile.settings[0]
            change = document.build_change(profile, setting["export_id"], "01")

            # Both codes read "Auto", so the labels match even though this is a
            # real change. The editor must compare raw values, not the display.
            self.assertEqual(change.old_display, change.new_display)
            self.assertNotEqual(change.new_raw, raw_value(setting))
            self.assertEqual(raw_value(setting), "00")
            self.assertEqual(change.new_raw, "01")

    def test_selecting_the_current_option_is_still_recognised_as_a_no_op(self):
        with tempfile.TemporaryDirectory() as temp:
            profile, document = build(Path(temp), DUPLICATE_LABELS)
            setting = profile.settings[0]
            change = document.build_change(profile, setting["export_id"], "00")
            self.assertEqual(change.new_raw, raw_value(setting))


class EncodingTests(unittest.TestCase):
    def test_queueing_a_non_latin1_value_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            profile, document = build(Path(temp), WIDE_VALUE)
            setting = profile.settings[0]
            with self.assertRaises(ValidationError):
                document.build_change(profile, setting["export_id"], "caf" + EURO_SIGN)

    def test_writer_reports_text_it_cannot_encode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile, document = build(root, WIDE_VALUE)
            setting = profile.settings[0]
            smuggled = PendingChange(
                export_id=str(setting["export_id"]),
                question=str(setting["question"]),
                kind="value",
                old_display="hello",
                new_display="caf" + EURO_SIGN,
                new_raw="caf" + EURO_SIGN,
                token_hex=str(setting["token_hex"]),
                offset_hex=str(setting["offset_hex"]),
                width_hex=str(setting["width_hex"]),
            )
            with self.assertRaises(ValidationError):
                document.write_modified_copy(root / "apply.txt", [smuggled])


class LoadFailureTests(unittest.TestCase):
    def test_missing_profile_reports_a_validation_error(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValidationError):
                ParsedProfile.load(Path(temp) / "absent.json")

    def test_malformed_profile_reports_a_validation_error(self):
        with tempfile.TemporaryDirectory() as temp:
            broken = Path(temp) / "profile.json"
            broken.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValidationError):
                ParsedProfile.load(broken)

    def test_profile_that_is_not_an_object_reports_a_validation_error(self):
        with tempfile.TemporaryDirectory() as temp:
            listed = Path(temp) / "profile.json"
            listed.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(ValidationError):
                ParsedProfile.load(listed)

    def test_missing_export_reports_a_validation_error(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValidationError):
                ScewinDocument(Path(temp) / "absent.txt")


class WriterFidelityTests(unittest.TestCase):
    r"""The written file must differ from the source only inside edited records.

    The document used to normalise line endings on the way in and re-expand every
    newline to CRLF on the way out, so a real AMISCE export - which carries stray
    "\r\r\n" sequences in its header - came back two bytes larger than it went in.
    """

    TEMPLATE = (
        "// AMISCE Utility. Ver 5.05.01.0002{n}"
        "HIICrc32= ABCD1234{n}{n}"
        "Setup Question\t= Fan Mode{n}"
        "Help String\t= Fan control{n}"
        "Token\t= 0100{n}"
        "Offset\t= 0010{n}"
        "Width\t= 01{n}"
        "BIOS Default\t= [00]Low{n}"
        "Options\t=*[00]Low{n}"
        "\t[01]Mid{n}"
    )

    # How a real AMISCE export is actually shaped: the doubled CRs appear on two
    # header lines only, and the records themselves are plain CRLF. Using them
    # inside a record would end the Options block at the blank line they create.
    REALISTIC = TEMPLATE.replace(
        "// AMISCE Utility. Ver 5.05.01.0002{n}HIICrc32= ABCD1234{n}",
        "// AMISCE Utility. Ver 5.05.01.0002\r\r\nHIICrc32= ABCD1234\r\r\n",
    )

    def write(self, root: Path, text: str) -> Path:
        nvram = root / "nvram.txt"
        nvram.write_bytes(text.encode("latin-1"))
        return nvram

    def test_a_no_op_render_reproduces_the_source_bytes(self):
        for label, newline in (("CRLF", "\r\n"), ("LF", "\n"), ("CR CR LF", "\r\r\n")):
            with self.subTest(line_endings=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                nvram = self.write(root, self.TEMPLATE.format(n=newline))
                output = ScewinDocument(nvram).write_modified_copy(root / "out.txt", [])
                self.assertEqual(output.read_bytes(), nvram.read_bytes())

    def test_only_the_edited_record_differs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nvram = self.write(root, self.REALISTIC.format(n="\r\n"))
            parsed = parse_sce_file(nvram, source="nvram", commented=False)
            profile_path = root / "profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "label": "Synthetic",
                        "metadata": parsed["metadata"],
                        "active_settings": [setting_to_dict(i) for i in parsed["settings"]],
                    }
                ),
                encoding="utf-8",
            )
            profile = ParsedProfile.load(profile_path)
            document = ScewinDocument(nvram)
            document.verify_profile(profile)

            setting = profile.settings[0]
            change = document.build_change(profile, setting["export_id"], "01")
            output = document.write_modified_copy(root / "out.txt", [change])

            before = nvram.read_bytes()
            after = output.read_bytes()
            span = document.records[setting["export_id"]]
            differing = [i for i in range(min(len(before), len(after))) if before[i] != after[i]]

            self.assertTrue(differing, "the change must actually be written")
            for offset in differing:
                self.assertTrue(
                    span.start <= offset < span.end,
                    f"byte {offset} changed outside the edited record {span.start}..{span.end}",
                )
            # The header's doubled CRs are part of what must survive untouched.
            self.assertIn(b"\r\r\n", after)


class IdentityTests(unittest.TestCase):
    def test_records_without_an_identity_are_not_compared(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text(HEADER + WITHOUT_IDENTITY, encoding="latin-1")
            second.write_text(HEADER + WITHOUT_IDENTITY, encoding="latin-1")

            result = compare_files(first, second)
            self.assertEqual(result.changed_count, 0)
            self.assertEqual([row.setting for row in result.rows], ["Real"])

    def test_has_identity_rejects_empty_and_placeholder_keys(self):
        self.assertFalse(has_identity(""))
        self.assertFalse(has_identity("::"))
        self.assertTrue(has_identity("0100:0010:01"))
        self.assertTrue(has_identity("0100::"))


if __name__ == "__main__":
    unittest.main()
