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

"""Quick Settings: presets, matching, and the tab that queues from them.

A preset names settings by token/offset/width and carries a tuned value for
each. These tests check that a preset file is validated on the way in, that
resolve() matches it to a loaded export the way Compare and Load NVRAM do, that
one control writes every identity it carries, and that the tab feeds the same
queue as the NVRAM table. The shipped LGA 1700 DDR5 preset is loaded too, so a
malformed edit to it fails here rather than in the window.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from bios_manager.core import ParsedProfile, ScewinDocument, ValidationError
from bios_manager.quick_settings import (
    SCHEMA_VERSION,
    changes_for,
    load_preset,
    load_presets,
    placeholders,
    preset_dir,
    resolve,
)
from bios_manager.scewin_parser import parse_sce_file, setting_to_dict

from importlib.util import find_spec

GUI_AVAILABLE = find_spec("PySide6") is not None

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HEADER = """// Script File Name : nvram.txt
// Created on 07/10/26 at 12:00:00
// AMISCE Utility. Ver 5.05.01.0002
HIICrc32= ABCD1234

"""

# Two channel copies of a timing, a ratio, a dropdown, and a value whose kind
# the preset gets wrong.
RECORDS = """Setup Question\t= tCL                          40
Help String\t= CAS latency, channel A.
Token\t= 1251
Offset\t= 0D49
Width\t= 02
BIOS Default\t= <0>
Value\t= <0>

Setup Question\t= tCL                          40
Help String\t= CAS latency, channel B.
Token\t= 12A1
Offset\t= 0DB3
Width\t= 02
BIOS Default\t= <0>
Value\t= <0>

Setup Question\t= P-Core Ratio
Help String\t= Sets the P-Core ratio.
Token\t= 28F6
Offset\t= 0C66
Width\t= 01
BIOS Default\t= <0>
Value\t= <54>

Setup Question\t= Intel C-State
Help String\t= CPU power management.
Token\t= 01F1
Offset\t= 0014
Width\t= 01
BIOS Default\t= [02]Auto
Options\t=[00]Disabled
\t[01]Enabled
\t*[02]Auto

Setup Question\t= Ring Ratio
Help String\t= Not a dropdown, whatever the preset says.
Token\t= 2936
Offset\t= 0CA8
Width\t= 01
BIOS Default\t= <0>
Value\t= <0>

"""


def preset_dict(**overrides) -> dict:
    data = {
        "schema_version": SCHEMA_VERSION,
        "name": "Synthetic",
        "platform": "test",
        "source": {"board": "bench"},
        "sections": [
            {
                "title": "CPU",
                "settings": [
                    {
                        "name": "P-Core Ratio",
                        "kind": "value",
                        "tuned": "54",
                        "identities": ["28F6:0C66:01"],
                    },
                    {
                        "name": "Intel C-State",
                        "kind": "options",
                        "tuned": "00",
                        "tuned_label": "Disabled",
                        "identities": ["01F1:0014:01"],
                    },
                    {
                        "name": "Ring Ratio",
                        "kind": "options",  # wrong on purpose: the record is a value
                        "tuned": "45",
                        "identities": ["2936:0CA8:01"],
                    },
                    {
                        "name": "Not Here",
                        "kind": "value",
                        "tuned": "1",
                        "identities": ["FFFF:FFFF:01"],
                    },
                ],
            },
            {
                "title": "RAM",
                "settings": [
                    {
                        "name": "tCL",
                        "kind": "value",
                        "tuned": "38",
                        "identities": ["1251:0D49:02", "12A1:0DB3:02"],
                    }
                ],
            },
        ],
    }
    data.update(overrides)
    return data


def write_preset(root: Path, filename: str = "synthetic.json", **overrides) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    path.write_text(json.dumps(preset_dict(**overrides)), encoding="utf-8")
    return path


def build_live(root: Path) -> tuple[ParsedProfile, ScewinDocument]:
    root.mkdir(parents=True, exist_ok=True)
    nvram = root / "nvram.txt"
    nvram.write_text(HEADER + RECORDS, encoding="latin-1")
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


class PresetFileTests(unittest.TestCase):
    def test_a_preset_loads_with_its_sections_in_order(self):
        with tempfile.TemporaryDirectory() as temp:
            preset = load_preset(write_preset(Path(temp)))
        self.assertEqual(preset.name, "Synthetic")
        self.assertEqual([section.title for section in preset.sections], ["CPU", "RAM"])
        self.assertEqual(preset.setting_count, 5)
        tcl = preset.sections[1].settings[0]
        self.assertEqual(tcl.identities, ("1251:0D49:02", "12A1:0DB3:02"))

    def test_option_codes_are_upper_cased_on_the_way_in(self):
        data = preset_dict()
        data["sections"][0]["settings"][1]["tuned"] = "0a"
        data["sections"][0]["settings"][1]["identities"] = ["01f1:0014:01"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "p.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            setting = load_preset(path).sections[0].settings[1]
        self.assertEqual(setting.tuned, "0A")
        self.assertEqual(setting.identities, ("01F1:0014:01",))

    def test_the_wrong_schema_a_duplicate_identity_and_a_bad_kind_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValidationError):
                load_preset(write_preset(root, "schema.json", schema_version=99))
            data = preset_dict()
            data["sections"][1]["settings"][0]["identities"].append("28F6:0C66:01")
            (root / "dup.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "appears twice"):
                load_preset(root / "dup.json")
            data = preset_dict()
            data["sections"][0]["settings"][0]["kind"] = "toggle"
            (root / "kind.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "kind"):
                load_preset(root / "kind.json")

    def test_a_broken_file_in_the_folder_does_not_take_the_others_down(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_preset(root, "b.json", name="Beta")
            write_preset(root, "a.json", name="Alpha")
            (root / "broken.json").write_text("{not json", encoding="utf-8")
            names = [preset.name for preset in load_presets(root)]
        self.assertEqual(names, ["Alpha", "Beta"])
        self.assertEqual(load_presets(Path(temp) / "missing"), [])

    def test_the_shipped_msi_z790_preset_is_valid_and_names_its_vendor(self):
        presets = {preset.platform: preset for preset in load_presets(preset_dir())}
        self.assertIn("msi-z790-ddr5", presets)
        preset = presets["msi-z790-ddr5"]
        # Identities are MSI's layout; the name and source must say so rather
        # than claim the socket.
        self.assertIn("MSI", preset.name)
        self.assertEqual(preset.source.get("vendor"), "MSI")
        self.assertEqual([s.title for s in preset.sections], ["CPU", "RAM", "GPU / PCIe"])
        names = [setting.name for section in preset.sections for setting in section.settings]
        # The value MSI pads onto a name must not survive into the control label.
        self.assertIn("tCL", names)
        self.assertIn("P-Core Ratio", names)
        self.assertNotIn("Fast Boot", names)  # the Windows one is dropped, the MRC one renamed
        self.assertIn("MRC Fast Boot", names)
        # The standing list: on the page even though stock and tuned agree on them.
        for name in ("Hyper-Threading", "tMOD", "tWR", "tRFC", "Extreme Memory Profile(XMP)"):
            self.assertIn(name, names)
        self.assertEqual(names.count("Hyper-Threading"), 1, "the global switch, not per core")
        self.assertNotIn("tZQCS", names)
        for name in names:
            self.assertNotRegex(name, r"\s{2,}", name)
        tcl = next(s for section in preset.sections for s in section.settings if s.name == "tCL")
        self.assertEqual(len(tcl.identities), 2, "both channel copies of tCL are one control")
        self.assertGreaterEqual(preset.setting_count, 60)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.preset = load_preset(write_preset(root))
        self.profile, self.document = build_live(root / "live")

    def tearDown(self):
        self._temp.cleanup()

    def rows(self) -> dict[str, object]:
        return {
            row.spec.name: row
            for _section, rows in resolve(self.preset, self.profile, self.document)
            for row in rows
        }

    def test_a_control_matches_every_identity_it_carries(self):
        tcl = self.rows()["tCL"]
        self.assertEqual(len(tcl.targets), 2)
        self.assertEqual(tcl.missing, ())
        self.assertTrue(tcl.available)
        self.assertEqual(tcl.current_display(), "0")
        self.assertFalse(tcl.matches_tuned)

    def test_matches_tuned_reads_the_live_value(self):
        rows = self.rows()
        self.assertTrue(rows["P-Core Ratio"].matches_tuned)
        self.assertFalse(rows["Intel C-State"].matches_tuned)
        self.assertEqual(rows["Intel C-State"].current_display(), "Auto")

    def test_an_identity_the_board_lacks_and_a_kind_mismatch_are_reported_not_guessed(self):
        rows = self.rows()
        self.assertFalse(rows["Not Here"].available)
        self.assertEqual(rows["Not Here"].missing, ("FFFF:FFFF:01",))
        self.assertEqual(rows["Not Here"].current_display(), "not on this board")
        self.assertFalse(rows["Ring Ratio"].available)
        self.assertEqual(len(rows["Ring Ratio"].blocked), 1)
        self.assertIn("preset expects options", rows["Ring Ratio"].blocked[0])

    def test_placeholders_carry_every_control_with_nothing_matched(self):
        laid_out = placeholders(self.preset)
        self.assertEqual([section.title for section, _ in laid_out], ["CPU", "RAM"])
        tcl = laid_out[1][1][0]
        self.assertEqual(tcl.targets, ())
        self.assertEqual(tcl.missing, tcl.spec.identities)
        self.assertFalse(tcl.available)

    def test_changes_for_writes_one_change_per_target(self):
        tcl = self.rows()["tCL"]
        changes, reset, errors = changes_for(tcl, "38", self.profile, self.document)
        self.assertEqual(errors, [])
        self.assertEqual(reset, [])
        self.assertEqual([(c.token_hex, c.new_display) for c in changes], [("1251", "38"), ("12A1", "38")])

    def test_changes_for_resets_a_target_already_at_the_value(self):
        ratio = self.rows()["P-Core Ratio"]
        changes, reset, errors = changes_for(ratio, "54", self.profile, self.document)
        self.assertEqual(changes, [])
        self.assertEqual(len(reset), 1)
        self.assertEqual(errors, [])

    def test_changes_for_refuses_an_option_code_the_setting_does_not_offer(self):
        cstate = self.rows()["Intel C-State"]
        changes, _reset, errors = changes_for(cstate, "7F", self.profile, self.document)
        self.assertEqual(changes, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("Intel C-State", errors[0])
        changes, _reset, errors = changes_for(cstate, "00", self.profile, self.document)
        self.assertEqual(errors, [])
        self.assertEqual(changes[0].new_display, "Disabled")


@unittest.skipUnless(GUI_AVAILABLE, "PySide6 is not installed")
class QuickTabTests(unittest.TestCase):
    """The tab queues into the same queue as the NVRAM table."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self._saved = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(root / "appdata")

        import bios_manager.gui as gui
        from PySide6.QtWidgets import QMessageBox

        preset = load_preset(write_preset(root))
        self._real_load = gui.load_presets
        gui.load_presets = lambda *a, **k: [preset]
        self._real_question = QMessageBox.question
        self._real_information = QMessageBox.information
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        self.window = gui.MainWindow(None, None, backend=None)
        self.profile, self.document = build_live(root / "live")

    def tearDown(self):
        import bios_manager.gui as gui
        from PySide6.QtWidgets import QMessageBox

        self.window.close()
        gui.load_presets = self._real_load
        QMessageBox.question = self._real_question
        QMessageBox.information = self._real_information
        if self._saved is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._saved
        self._temp.cleanup()

    def load(self):
        self.window._show_nvram(self.profile, self.document, "test")

    def row(self, name: str):
        return next(row for row in self.window.quick_rows if row.resolved.spec.name == name)

    def test_the_tab_sits_between_nvram_and_pending_changes(self):
        from PySide6.QtWidgets import QTabWidget

        tabs = self.window.findChild(QTabWidget)
        titles = [tabs.tabText(i) for i in range(tabs.count())]
        self.assertEqual(titles[:3], ["NVRAM", "Quick Settings", "Pending Changes"])

    def test_nothing_can_be_queued_before_an_nvram_is_loaded(self):
        self.assertEqual(len(self.window.quick_rows), 5)
        for row in self.window.quick_rows:
            self.assertFalse(row.button.isEnabled(), row.resolved.spec.name)
            self.assertEqual(row.current.text(), "—")

    def test_the_tab_shows_no_tuned_values_and_no_bulk_buttons(self):
        from PySide6.QtWidgets import QLabel, QPushButton

        labels = {label.text() for label in self.window.findChildren(QLabel)}
        self.assertNotIn("Tuned", labels)
        self.assertIn("New value", labels)
        buttons = [b.text() for b in self.window.findChildren(QPushButton)]
        self.assertFalse(any("tuned" in text.lower() for text in buttons), buttons)
        self.assertEqual(buttons.count("Queue"), 5)

    def test_loading_an_export_fills_current_values_and_states(self):
        self.load()
        self.assertEqual(self.row("tCL").current.text(), "0")
        self.assertEqual(self.row("P-Core Ratio").state.text(), "")
        self.assertEqual(self.row("Not Here").state.text(), "not on this board")
        self.assertFalse(self.row("Not Here").button.isEnabled())
        self.assertEqual(self.row("Ring Ratio").state.text(), "blocked")
        self.assertTrue(self.row("tCL").button.isEnabled())

    def test_the_editor_starts_on_the_current_value(self):
        self.load()
        self.assertEqual(self.row("tCL").editor.text(), "0")
        self.assertEqual(self.row("P-Core Ratio").editor.text(), "54")
        cstate = self.row("Intel C-State").editor
        self.assertEqual(cstate.currentData(), "02")  # Auto, the live value
        self.assertEqual(cstate.count(), 3)
        # Queue with nothing changed is a no-op rather than a surprise write.
        self.row("tCL").button.click()
        self.assertEqual(self.window.changes, {})

    def test_queueing_one_row_writes_every_identity_and_shows_in_pending_changes(self):
        self.load()
        self.row("tCL").editor.setText("38")
        self.row("tCL").button.click()
        self.assertEqual(sorted(c.token_hex for c in self.window.changes.values()), ["1251", "12A1"])
        self.assertEqual(self.window.change_model.rowCount(), 2)
        self.assertEqual(self.row("tCL").state.text(), "queued → 38")

    def test_an_edited_value_is_what_gets_queued_and_the_current_value_resets_it(self):
        self.load()
        ratio = self.row("P-Core Ratio")
        ratio.editor.setText("55")
        ratio.button.click()
        self.assertEqual([c.new_display for c in self.window.changes.values()], ["55"])
        ratio.editor.setText("54")
        ratio.button.click()
        self.assertEqual(self.window.changes, {})
        self.assertEqual(ratio.state.text(), "")

    def test_a_dropdown_row_queues_the_selected_option_and_a_current_pick_resets_it(self):
        self.load()
        cstate = self.row("Intel C-State")
        cstate.editor.setCurrentIndex(cstate.editor.findData("00"))
        cstate.button.click()
        self.assertEqual([c.new_display for c in self.window.changes.values()], ["Disabled"])
        self.assertEqual(cstate.state.text(), "queued → Disabled")
        cstate.editor.setCurrentIndex(cstate.editor.findData("01"))
        cstate.button.click()
        self.assertEqual([c.new_display for c in self.window.changes.values()], ["Enabled"])
        cstate.editor.setCurrentIndex(cstate.editor.findData("02"))  # the live value, Auto
        cstate.button.click()
        self.assertEqual(self.window.changes, {})
        self.assertEqual(cstate.state.text(), "")

    def test_the_notice_says_how_many_controls_apply(self):
        self.assertIn("No NVRAM loaded", self.window.quick_notice.text())
        self.load()
        # tCL, P-Core Ratio, C-State apply; Ring Ratio is blocked; Not Here is absent.
        self.assertIn("3 of 5 controls apply", self.window.quick_notice.text())

    def test_another_vendors_export_is_reported_as_a_mismatch_not_a_page_of_dead_rows(self):
        import bios_manager.gui as gui

        data = preset_dict(name="Other Vendor Z790")
        data["source"] = {"vendor": "ASUS", "board": "some ASUS board"}
        # One identity per control, unique across sections and absent from RECORDS.
        for number, setting in enumerate(
            setting for section in data["sections"] for setting in section["settings"]
        ):
            setting["identities"] = [f"EE{number:02X}:00{number:02X}:01"]
        root = Path(self._temp.name)
        (root / "foreign.json").write_text(json.dumps(data), encoding="utf-8")
        gui.load_presets = lambda *a, **k: [load_preset(root / "foreign.json")]
        window = gui.MainWindow(None, None, backend=None)
        try:
            window._show_nvram(self.profile, self.document, "test")
            notice = window.quick_notice.text()
            self.assertIn("does not match the loaded NVRAM", notice)
            self.assertIn("0 of 5", notice)
            self.assertIn("some ASUS board", notice)
            self.assertIn("ASRock", notice)
            self.assertTrue(all(not row.button.isEnabled() for row in window.quick_rows))
        finally:
            window.close()

    def test_showing_another_export_rebuilds_the_rows_against_it(self):
        self.load()
        self.row("tCL").editor.setText("38")
        self.row("tCL").button.click()
        first = self.row("tCL")
        self.load()  # _show_nvram clears the queue and rebuilds
        self.assertEqual(self.window.changes, {})
        self.assertIsNot(self.row("tCL"), first)
        self.assertEqual(self.row("tCL").state.text(), "")


if __name__ == "__main__":
    unittest.main()
