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

from bios_manager.core import ParsedProfile, ScewinDocument, ValidationError
from bios_manager.scewin_backend import (
    remap_changes,
    verify_changes,
    write_live_profile,
)


# Committed alongside the tests so a fresh clone can run them. It is a small
# synthetic export, not a board dump, but it keeps the record shapes the real
# ones have: a blocked setting, an N/A question, options, and numeric values.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NVRAM = FIXTURES / "nvram.txt"
DUPES = FIXTURES / "Dupes.txt"


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The profile is derived from the committed export instead of being
        # committed next to it, so the two cannot drift apart.
        cls._temp = tempfile.TemporaryDirectory()
        cls.profile_path = Path(cls._temp.name) / "profile.json"
        write_live_profile(NVRAM, DUPES, cls.profile_path)
        cls.profile = ParsedProfile.load(cls.profile_path)
        cls.document = ScewinDocument(NVRAM)
        cls.by_name = {
            item.get("question"): item for item in cls.profile.settings
        }

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_live_profile_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "live.json"
            write_live_profile(NVRAM, DUPES, output)
            profile = ParsedProfile.load(output)
            self.assertEqual(profile.hii_crc32, "B176E215")
            self.assertEqual(len(profile.settings), 8)
            self.assertEqual(profile.settings[0]["question"], "Multi-Theme")
            self.assertEqual(profile.label, "NVRAM")

    def test_remap_unchanged_setting(self):
        setting = self.by_name["Extreme Memory Profile(X.M.P.)"]
        queued = self.document.build_change(self.profile, setting["export_id"], "05")
        remapped = remap_changes(
            self.profile, self.profile, self.document, [queued]
        )
        self.assertEqual(len(remapped), 1)
        self.assertEqual(remapped[0].new_raw, "05")

    def test_remap_rejects_stale_value(self):
        setting = self.by_name["Extreme Memory Profile(X.M.P.)"]
        queued = self.document.build_change(self.profile, setting["export_id"], "05")
        changed_profile = ParsedProfile.load(self.profile_path)
        changed_setting = changed_profile.by_id()[setting["export_id"]]
        changed_setting["current_code_hex"] = "05"
        changed_setting["current_label"] = "User Profile"
        with self.assertRaises(ValidationError):
            remap_changes(
                self.profile, changed_profile, self.document, [queued]
            )

    def test_verification(self):
        setting = self.by_name["Extreme Memory Profile(X.M.P.)"]
        queued = self.document.build_change(self.profile, setting["export_id"], "05")
        self.assertTrue(verify_changes(self.profile, [queued]))

        verified_profile = ParsedProfile.load(self.profile_path)
        verified_setting = verified_profile.by_id()[setting["export_id"]]
        verified_setting["current_code_hex"] = "05"
        verified_setting["current_label"] = "User Profile"
        self.assertEqual(verify_changes(verified_profile, [queued]), [])


if __name__ == "__main__":
    unittest.main()
