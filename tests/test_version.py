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

"""One version, written once and agreed everywhere it appears.

bios_manager/version.py is the only place it is written. The window title and
the executable's file properties read from it directly; the changelog and the
README cannot, so they are checked against it here. A release where those
disagree is one where a build cannot be identified from its own properties.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import bios_manager
from bios_manager import version
from bios_manager.version import APP_NAME, VERSION_TUPLE

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class VersionTests(unittest.TestCase):
    def test_it_is_three_numbers(self):
        self.assertRegex(version.__version__, r"^\d+\.\d+\.\d+$")

    def test_the_windows_tuple_is_the_version_plus_a_build_field(self):
        expected = tuple(int(part) for part in version.__version__.split(".")) + (0,)
        self.assertEqual(VERSION_TUPLE, expected)
        self.assertEqual(len(VERSION_TUPLE), 4)

    def test_the_package_re_exports_it_rather_than_declaring_its_own(self):
        self.assertEqual(bios_manager.__version__, version.__version__)
        self.assertEqual(bios_manager.APP_NAME, APP_NAME)

    def test_nothing_else_declares_a_version(self):
        # The point of version.py: grep the tracked sources for a second
        # literal and fail rather than let two of them drift apart.
        pattern = re.compile(r"""__version__\s*=\s*["']""")
        declaring = [
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*.py")
            if ".venv" not in path.parts and pattern.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(declaring, ["bios_manager/version.py"])

    def test_the_spec_generates_the_resource_from_this_module(self):
        # Without this the EXE builds fine and carries a stale version, or none.
        spec = read("RochNVRAM.spec")
        self.assertIn("from bios_manager.version import", spec)
        self.assertIn("version=_version_resource", spec)
        # A hard-coded resource file would be a second copy to forget.
        self.assertNotIn("version='file_version_info.txt'", spec)
        self.assertFalse((ROOT / "file_version_info.txt").exists())

    def test_the_changelog_leads_with_this_version(self):
        first = re.search(r"^## (\S+)", read("CHANGELOG.md"), re.MULTILINE)
        self.assertIsNotNone(first, "no version heading in CHANGELOG.md")
        self.assertEqual(first.group(1), version.__version__)

    def test_the_readme_names_this_version(self):
        self.assertIn(f"# {APP_NAME} {version.__version__}", read("README.md"))

    def test_the_window_is_titled_from_this_module(self):
        gui = read("bios_manager/gui.py")
        self.assertIn("from .version import APP_NAME", gui)
        self.assertNotIn('APP_NAME = "', gui)


if __name__ == "__main__":
    unittest.main()
