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

from bios_manager.core import ParsedProfile, ScewinDocument, ValidationError, write_transaction
from bios_manager.scewin_backend import write_live_profile


# Committed alongside the tests so a fresh clone can run them. It is a small
# synthetic export, not a board dump, but it keeps the record shapes the real
# ones have: a blocked setting, an N/A question, options, and numeric values.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NVRAM = FIXTURES / "nvram.txt"
DUPES = FIXTURES / "Dupes.txt"


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The profile is derived from the committed export instead of being
        # committed next to it, so the two cannot drift apart.
        cls._temp = tempfile.TemporaryDirectory()
        cls.profile_path = Path(cls._temp.name) / "profile.json"
        write_live_profile(NVRAM, DUPES, cls.profile_path)
        cls.profile = ParsedProfile.load(cls.profile_path)
        cls.document = ScewinDocument(NVRAM)
        cls.document.verify_profile(cls.profile)
        cls.by_id = cls.profile.by_id()

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def find(self, question: str):
        matches = [item for item in self.profile.settings if item.get("question") == question]
        self.assertTrue(matches, question)
        return matches[0]

    def test_profile_matches_source(self):
        self.assertEqual(self.profile.hii_crc32, "B176E215")
        self.assertEqual(len(self.document.records), 8)


    def test_profile_preserves_nvram_order(self):
        expected = [
            "Multi-Theme",
            "CPU Temp Loading Time",
            "N/A",
            "BCLK Output Source",
            "CPU Base Clock 100.00MHz",
        ]
        actual = [item.get("question") for item in self.profile.settings[:5]]
        self.assertEqual(actual, expected)

    def test_option_change_moves_star(self):
        setting = self.find("Extreme Memory Profile(X.M.P.)")
        change = self.document.build_change(self.profile, setting["export_id"], "05")
        rendered = self.document.render([change])
        block_start = rendered.index("Setup Question\t= Extreme Memory Profile(X.M.P.)")
        block_end = rendered.index("Setup Question\t=", block_start + 1)
        block = rendered[block_start:block_end]
        self.assertIn("Options\t=[00]Disabled", block)
        self.assertIn("*[05]User Profile", block)
        self.assertNotIn("Options\t=*[00]Disabled", block)

    def test_numeric_change(self):
        setting = self.find("Performance CPU Clock Ratio 37")
        change = self.document.build_change(self.profile, setting["export_id"], "0x37")
        rendered = self.document.render([change])
        block_start = rendered.index("Setup Question\t= Performance CPU Clock Ratio 37")
        block_end = rendered.index("Setup Question\t=", block_start + 1)
        block = rendered[block_start:block_end]
        self.assertIn("Value\t=<55>", block)

    def test_warned_setting_is_blocked(self):
        setting = self.find("Multi-Theme")
        with self.assertRaises(ValidationError):
            self.document.build_change(self.profile, setting["export_id"], "1A")

    def test_original_cannot_be_overwritten(self):
        setting = self.find("Extreme Memory Profile(X.M.P.)")
        change = self.document.build_change(self.profile, setting["export_id"], "05")
        with self.assertRaises(ValidationError):
            self.document.write_modified_copy(NVRAM, [change])

    def test_write_outputs(self):
        setting = self.find("Extreme Memory Profile(X.M.P.)")
        change = self.document.build_change(self.profile, setting["export_id"], "05")
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            nvram_output = self.document.write_modified_copy(temp_path / "modified.txt", [change])
            transaction_output = write_transaction(
                temp_path / "transaction.json", self.profile, self.document, [change]
            )
            self.assertTrue(nvram_output.exists())
            payload = json.loads(transaction_output.read_text(encoding="utf-8"))
            self.assertTrue(payload["dry_run_only"])
            self.assertEqual(len(payload["changes"]), 1)


if __name__ == "__main__":
    unittest.main()
