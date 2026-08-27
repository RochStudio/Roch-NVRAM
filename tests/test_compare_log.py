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

import tempfile
import unittest
from pathlib import Path

from bios_manager.activity_log import ActivityLogger
from bios_manager.compare_tools import compare_files, export_compare_csv


HEADER = """// Script File Name : nvram.txt
// Created on 07/10/26 at 12:00:00
// AMISCE Utility. Ver 5.05.01.0002
HIICrc32= ABCD1234

"""


def make_nvram(option_code: str = "00", value: str = "100", hii: str = "ABCD1234") -> str:
    option_00 = "*" if option_code == "00" else ""
    option_01 = "*" if option_code == "01" else ""
    return (HEADER.replace("ABCD1234", hii) + f"""Setup Question\t= Test Option
Help String\t= Test option help
Token\t= 10
Offset\t= 20
Width\t= 01
BIOS Default\t= [00]Disabled
Options\t={option_00}[00]Disabled
         {option_01}[01]Enabled

Setup Question\t= Test Value
Help String\t= Test value help
Token\t= 11
Offset\t= 21
Width\t= 02
BIOS Default\t= <100>
Value\t= <{value}>

""")


class CompareTests(unittest.TestCase):
    def test_compare_detects_changed_values_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = root / "stock.txt"
            overclocked = root / "overclocked.txt"
            stock.write_text(make_nvram(), encoding="latin-1")
            overclocked.write_text(make_nvram(option_code="01", value="120"), encoding="latin-1")

            result = compare_files(stock, overclocked)
            self.assertTrue(result.hii_match)
            self.assertEqual(result.changed_count, 2)
            self.assertEqual([row.setting for row in result.rows], ["Test Option", "Test Value"])
            self.assertEqual(result.rows[0].stock_value, "Disabled [00]")
            self.assertEqual(result.rows[0].overclocked_value, "Enabled [01]")
            self.assertEqual(result.rows[1].stock_value, "<100>")
            self.assertEqual(result.rows[1].overclocked_value, "<120>")

    def test_name_only_difference_is_not_a_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = root / "stock.txt"
            overclocked = root / "overclocked.txt"
            # Same token/offset/width and same value, but the Setup Question label
            # differs between the two BIOS builds. This must not count as changed.
            stock.write_text(make_nvram(), encoding="latin-1")
            overclocked.write_text(
                make_nvram().replace("Test Value", "Test Value 1.341V"),
                encoding="latin-1",
            )
            result = compare_files(stock, overclocked)
            renamed = next(row for row in result.rows if "1.341V" in row.setting)
            self.assertFalse(renamed.changed)
            self.assertEqual(renamed.status, "Same")
            self.assertEqual(result.changed_count, 0)

    def test_compare_reports_hii_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = root / "stock.txt"
            overclocked = root / "overclocked.txt"
            stock.write_text(make_nvram(), encoding="latin-1")
            overclocked.write_text(make_nvram(hii="FFFF0000"), encoding="latin-1")
            result = compare_files(stock, overclocked)
            self.assertFalse(result.hii_match)
            self.assertEqual(result.changed_count, 0)

    def test_csv_exports_changed_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = root / "stock.txt"
            overclocked = root / "overclocked.txt"
            stock.write_text(make_nvram(), encoding="latin-1")
            overclocked.write_text(make_nvram(value="101"), encoding="latin-1")
            result = compare_files(stock, overclocked)
            output = export_compare_csv(root / "changes.csv", result, changed_only=True)
            text = output.read_text(encoding="utf-8-sig")
            self.assertIn("NVRAM 1 Value,NVRAM 2 Value", text)
            self.assertIn("Test Value", text)
            self.assertNotIn("Test Option,", text)


class ActivityLogTests(unittest.TestCase):
    def test_log_contains_local_date_time_event_and_message(self):
        with tempfile.TemporaryDirectory() as temp:
            logger = ActivityLogger(temp)
            line = logger.write("nvram_open", "Opened stock.txt")
            self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| NVRAM_OPEN \|")
            self.assertIn("Opened stock.txt", logger.read_text())
            self.assertEqual(logger.log_path.name, "nvram.log")
            self.assertTrue(logger.log_path.is_file())


if __name__ == "__main__":
    unittest.main()
