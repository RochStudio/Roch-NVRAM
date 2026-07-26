from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bios_manager.core import ParsedProfile, ScewinDocument, ValidationError, write_transaction


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "z890_tachyon_nvram_parsed.json"
NVRAM = ROOT / "data" / "z890_tachyon_nvram.txt"


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = ParsedProfile.load(PROFILE)
        cls.document = ScewinDocument(NVRAM)
        cls.document.verify_profile(cls.profile)
        cls.by_id = cls.profile.by_id()

    def find(self, question: str):
        matches = [item for item in self.profile.settings if item.get("question") == question]
        self.assertTrue(matches, question)
        return matches[0]

    def test_profile_matches_source(self):
        self.assertEqual(self.profile.hii_crc32, "B176E215")
        self.assertEqual(len(self.document.records), 8299)


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
