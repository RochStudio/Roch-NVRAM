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

"""Tests for the on-demand Export / Import round trip.

The window no longer reads the firmware when it opens. Export reads the live
NVRAM on demand, Import writes the reviewed queue back, and Load NVRAM fills
that queue from a saved export through MainWindow._differences_from.

The tests that need PySide6 skip when it is not installed, because
run_tests.bat uses the system interpreter rather than the .venv.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

# Widgets are built here, so ask Qt for a platform that needs no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bios_manager.compare_tools import load_snapshot
from bios_manager.core import ParsedProfile, ScewinDocument
from bios_manager.scewin_parser import parse_sce_file, setting_to_dict

try:
    from bios_manager.gui import MainWindow, SavedNvramDiff

    GUI_AVAILABLE = True
except ImportError:  # PySide6 is not a test dependency
    GUI_AVAILABLE = False


HEADER = """// Script File Name : nvram.txt
// Created on 07/10/26 at 12:00:00
// AMISCE Utility. Ver 5.05.01.0002
HIICrc32= ABCD1234

"""

RECORDS = """Setup Question\t= Fan Curve
Help String\t= Fan curve selection
Token\t= 0101
Offset\t= 0011
Width\t= 01
BIOS Default\t= [00]Auto
Options\t=*[00]Auto
\t[01]Balanced
\t[02]Manual

Setup Question\t= Board Label
Help String\t= Free text field
Token\t= 0102
Offset\t= 0012
Width\t= 10
BIOS Default\t= <hello>
Value\t= <hello>

"""


def write_nvram(root: Path, records: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    nvram = root / "nvram.txt"
    nvram.write_text(HEADER + records, encoding="latin-1")
    return nvram


def build_live(root: Path, records: str) -> tuple[ParsedProfile, ScewinDocument]:
    nvram = write_nvram(root, records)
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


@unittest.skipUnless(GUI_AVAILABLE, "PySide6 is not installed")
class SavedNvramMatchingTests(unittest.TestCase):
    """MainWindow._differences_from is what Load NVRAM queues from."""

    def differences(self, root: Path, saved_records: str) -> SavedNvramDiff:
        profile, document = build_live(root / "live", RECORDS)
        saved = write_nvram(root / "saved", saved_records)
        window = SimpleNamespace(profile=profile, document=document)
        return MainWindow._differences_from(window, load_snapshot(saved))

    def test_a_changed_option_is_queued_and_matches_are_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            changed = RECORDS.replace("Options\t=*[00]Auto", "Options\t=[00]Auto").replace(
                "\t[02]Manual", "\t*[02]Manual"
            )
            diff = self.differences(Path(temp), changed)

            self.assertEqual(len(diff.queued), 1)
            self.assertEqual(diff.queued[0].question, "Fan Curve")
            self.assertEqual(diff.queued[0].new_raw, "02")
            self.assertEqual(diff.queued[0].new_display, "Manual")
            self.assertEqual(diff.already, 1)
            self.assertEqual(diff.missing, 0)
            self.assertEqual(diff.blocked, [])

    def test_a_changed_value_is_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            diff = self.differences(Path(temp), RECORDS.replace("Value\t= <hello>", "Value\t= <world>"))

            self.assertEqual(len(diff.queued), 1)
            self.assertEqual(diff.queued[0].question, "Board Label")
            self.assertEqual(diff.queued[0].new_raw, "world")
            self.assertEqual(diff.already, 1)

    def test_an_identical_file_queues_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            diff = self.differences(Path(temp), RECORDS)

            self.assertEqual(diff.queued, [])
            self.assertEqual(diff.already, 2)
            self.assertEqual(diff.missing, 0)
            self.assertEqual(diff.blocked, [])

    def test_settings_absent_from_the_live_export_are_counted_not_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            extra = RECORDS + """Setup Question\t= Not On This Board
Help String\t= only in the saved file
Token\t= 0999
Offset\t= 0999
Width\t= 01
BIOS Default\t= <1>
Value\t= <2>

"""
            diff = self.differences(Path(temp), extra)

            self.assertEqual(diff.queued, [])
            self.assertEqual(diff.missing, 1)
            self.assertEqual(diff.already, 2)

    def test_a_type_change_is_blocked_rather_than_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            # Same token/offset/width as the live Fan Curve, but a value record.
            retyped = RECORDS.replace(
                "BIOS Default\t= [00]Auto\nOptions\t=*[00]Auto\n\t[01]Balanced\n\t[02]Manual",
                "BIOS Default\t= <1>\nValue\t= <2>",
            )
            diff = self.differences(Path(temp), retyped)

            self.assertEqual(diff.queued, [])
            self.assertEqual(len(diff.blocked), 1)
            self.assertIn("type differs", diff.blocked[0])


@unittest.skipUnless(GUI_AVAILABLE, "PySide6 is not installed")
class StartupTests(unittest.TestCase):
    def test_the_window_no_longer_reads_the_firmware_on_open(self):
        import inspect

        from bios_manager import gui

        source = inspect.getsource(gui.run)
        self.assertNotIn("export_current", source)
        self.assertIn("export_live_nvram", inspect.getsource(gui.MainWindow._settings_tab))

    def test_export_reads_the_live_nvram_and_import_writes_the_queue(self):
        import inspect

        self.assertIn("export_current", inspect.getsource(MainWindow._run_live_export))
        # Import is the apply path, so the backup and verification still surround it.
        self.assertIn("execute_apply", inspect.getsource(MainWindow.apply_changes))
        self.assertFalse(hasattr(MainWindow, "import_nvram"))


@unittest.skipUnless(GUI_AVAILABLE, "PySide6 is not installed")
class ButtonTests(unittest.TestCase):
    """The NVRAM tab presents the round trip as Export and Import."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # The window builds an ActivityLogger and an NvramCatalog rooted at
        # LOCALAPPDATA. Point it at a temporary directory so running the tests
        # never touches the real NVRAM catalog or its archive.
        self._temp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self._temp.name

        from bios_manager.scewin_backend import ScewinBackend

        self.window = MainWindow(None, None, backend=ScewinBackend(Path.cwd()))

    def tearDown(self):
        self.window.close()
        if self._saved is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._saved
        self._temp.cleanup()

    def buttons(self) -> dict[str, bool]:
        from PySide6.QtWidgets import QPushButton

        return {b.text(): b.isEnabled() for b in self.window.findChildren(QPushButton)}

    def test_the_nvram_tab_row_leads_with_export_and_import(self):
        from PySide6.QtWidgets import QPushButton

        row = [b.text() for b in self.window.settings_table.parent().findChildren(QPushButton)]
        self.assertEqual(row, ["Export", "Import", "Queue selected change", "Load NVRAM..."])
        # The two halves of the round trip sit together, ahead of the editing controls.
        self.assertEqual(row.index("Import"), row.index("Export") + 1)

    def test_only_export_works_before_an_nvram_is_loaded(self):
        state = self.buttons()
        self.assertTrue(state["Export"])
        for label in ("Queue selected change", "Load NVRAM...", "Import"):
            self.assertFalse(state[label], label)

    def test_the_old_labels_are_gone(self):
        state = self.buttons()
        for label in ("Apply", "Export NVRAM", "Import NVRAM..."):
            self.assertNotIn(label, state)

    def test_a_busy_cycle_leaves_disabled_actions_disabled(self):
        # The window is disabled wholesale while SCEWIN runs. Restoring it must
        # not enable buttons that have no NVRAM to act on.
        with self.window._busy("working..."):
            pass
        self.assertFalse(self.buttons()["Import"])
        self.assertTrue(self.buttons()["Export"])

    def test_the_window_recovers_when_a_busy_block_raises(self):
        from PySide6.QtWidgets import QApplication

        with self.assertRaises(RuntimeError):
            with self.window._busy("failing..."):
                raise RuntimeError("boom")
        # Otherwise the error dialog would open over a dead window under a
        # wait cursor that nothing ever pops.
        self.assertTrue(self.window.isEnabled())
        self.assertIsNone(QApplication.overrideCursor())

    def test_offline_mode_offers_neither_export_nor_import(self):
        offline = MainWindow(None, None, backend=None)
        try:
            from PySide6.QtWidgets import QPushButton

            labels = {b.text() for b in offline.findChildren(QPushButton)}
            self.assertNotIn("Export", labels)
            self.assertNotIn("Import", labels)
        finally:
            offline.close()


if __name__ == "__main__":
    unittest.main()
