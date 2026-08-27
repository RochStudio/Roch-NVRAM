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

from bios_manager.nvram_catalog import NvramCatalog


NVRAM_TEXT = """// Script File Name : nvram.txt
// Created on 07/10/26 at 12:00:00
// AMISCE Utility. Ver 5.05.01.0002
HIICrc32= ABCD1234

Setup Question\t= Test Option
Token\t= 10
Offset\t= 20
Width\t= 01
"""


class CatalogTests(unittest.TestCase):
    def _make_nvram(self, root: Path, name: str = "nvram.txt") -> Path:
        path = root / name
        path.write_text(NVRAM_TEXT, encoding="latin-1")
        return path

    def test_record_appends_dated_entry_with_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = NvramCatalog(root)
            source = self._make_nvram(root)
            entry = catalog.record(
                "Startup",
                source,
                "abcd1234",
                42,
                "deadbeef" * 8,
                amisce_version="5.05.01.0002",
                archive_source=source,
            )
            self.assertRegex(entry.timestamp, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
            self.assertEqual(entry.hii_crc32, "ABCD1234")
            self.assertEqual(entry.amisce_version, "5.05.01.0002")
            self.assertEqual(catalog.catalog_path.name, "nvram_catalog.jsonl")

            entries = catalog.entries()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].settings, 42)

    def test_actual_nvram_is_archived_verbatim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = NvramCatalog(root)
            source = self._make_nvram(root)
            entry = catalog.record(
                "Startup", source, "ABCD1234", 1, "a" * 64, archive_source=source
            )
            self.assertTrue(entry.archived_path)
            archived = Path(entry.archived_path)
            self.assertTrue(archived.is_file())
            self.assertEqual(
                archived.read_text(encoding="latin-1"),
                source.read_text(encoding="latin-1"),
            )

    def test_identical_nvram_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = NvramCatalog(root)
            source = self._make_nvram(root)
            first = catalog.record(
                "Startup", source, "ABCD1234", 1, "b" * 64, archive_source=source
            )
            second = catalog.record(
                "Manual open", source, "ABCD1234", 1, "b" * 64, archive_source=source
            )
            self.assertEqual(first.archived_path, second.archived_path)
            self.assertEqual(len(list(catalog.archive_dir.glob("*.txt"))), 1)
            self.assertEqual(len(catalog.entries()), 2)

    def test_export_csv_contains_version_and_archive_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = NvramCatalog(root)
            source = self._make_nvram(root)
            catalog.record(
                "Startup",
                source,
                "ABCD1234",
                1,
                "c" * 64,
                amisce_version="5.05.01.0002",
                archive_source=source,
            )
            output = catalog.export_csv(root / "out" / "catalog.csv")
            text = output.read_text(encoding="utf-8-sig")
            self.assertIn("SCEWIN Version", text)
            self.assertIn("Archived NVRAM File", text)
            self.assertIn("5.05.01.0002", text)


if __name__ == "__main__":
    unittest.main()
