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

"""Build a Quick Settings preset from a stock export and a tuned export.

    py tools/make_quick_settings.py stock.txt tuned.txt ^
        --name "LGA 1700 DDR5" --platform lga1700-ddr5 ^
        --out assets/quick_settings/lga1700_ddr5.json

Every setting whose value differs between the two files is a candidate. The
RULES table below sorts candidates into the CPU, RAM, and GPU sections by name
and help text, and drops the ones that change between exports without being
overclocking controls (fan curves, the driver installer, DMI lane coefficients,
derived read-backs). Anything the rules do not place is listed at the end so a
new board's leftovers are visible rather than silently lost.

Candidates with the same name, kind, and tuned value collapse into one control
that writes all of their identities -- the per-channel copies of a DDR5 timing.
Same name but a different tuned value stays separate, with the value in the name.

The EXTRA list adds the controls that belong on the page whether or not the
tuned export touched them -- Hyper-Threading, XMP, the common timings -- when
the exports have them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bios_manager.compare_tools import has_identity, identity_without_crc  # noqa: E402
from bios_manager.core import raw_value  # noqa: E402
from bios_manager.quick_settings import SCHEMA_VERSION  # noqa: E402
from bios_manager.scewin_parser import parse_sce_file, setting_to_dict  # noqa: E402

CPU, RAM, GPU, SKIP = "CPU", "RAM", "GPU / PCIe", None

# (section, name pattern, help pattern). First match wins; None means drop.
RULES: list[tuple[str | None, str, str]] = [
    # Not overclocking controls, even though a tuned export changes them.
    (SKIP, r"^tZQCS$", r""),  # ZQ calibration interval, not a tuning knob
    (SKIP, r"Fan\d* level|Fan Speed", r""),
    (SKIP, r"Driver Utility Installer", r""),
    (SKIP, r"^DMI Gen", r""),
    (SKIP, r"^M2_\d", r""),  # NVMe slot link speed
    (SKIP, r"^CRSV\d", r""),  # reserved V/F points the firmware fills in
    (SKIP, r"Core Dis Value", r""),  # internal core-disable mask
    (SKIP, r"Adjusted DRAM Frequency", r""),  # read-back of the applied clock
    (SKIP, r"High Temperature Threshold", r""),  # tuned value is a Reserved code
    (SKIP, r"Core Voltage Offset Mode", r""),  # tuned code has no label
    (SKIP, r"^Fast Boot$", r"Windows"),  # OS fast startup, not the MRC one
    # Memory: timings, clocks, training, the DIMM rails and the IMC rails.
    (RAM, r"^Fast Boot$", r"MRC"),
    (RAM, r"^t[A-Z][A-Za-z_0-9]*$", r""),
    (
        RAM,
        r"DRAM|Memory|IMC|Gear|Command Rate|Power Down Mode|Vddq Training|Round Trip|"
        r"SA GV|Extreme Memory|XMP|Try It",
        r"",
    ),
    (RAM, r"^(VDD|VDDQ|VPP) ", r""),
    (RAM, r"^CPU (SA|VDDQ|VDD2) Voltage", r""),
    # The graphics slot and the PCIe features a GPU cares about.
    (GPU, r"Re-?Size BAR|Resizable BAR|^PCI_E1 |PCIe Native Power", r""),
    # Everything else about the processor.
    (
        CPU,
        r"CPU|Core|Ring|EIST|Speed Shift|Turbo|C-State|Power Limit|AVX|TVB|CEP|"
        r"Loadline|Over Voltage|Over Current|Switching Frequency|VRM|Cooler|Current Limit|"
        r"Hyper|Lite Load|TjMax|Package C|Thermal Velocity",
        r"",
    ),
]

# Controls that belong on the page even when the two exports agree on them: the
# switches and timings anyone tuning this platform reaches for, whether or not
# the tuned export happened to touch them. (canonical name, section). Matched
# exactly, so "Hyper-Threading" is the global switch, not the eight per-core
# copies. A name the exports do not have is simply not emitted.
EXTRA: list[tuple[str, str]] = [
    ("Hyper-Threading", CPU),
    ("Active P-Cores", CPU),
    ("E-Core Ratio", CPU),
    ("E-Core Ratio Apply Mode", CPU),
    ("CPU Lite Load", CPU),
    ("CPU Lite Load Control", CPU),
    ("AC Loadline", CPU),
    ("DC Loadline", CPU),
    ("CPU Core Voltage Offset Mode", CPU),
    ("CPU Core Voltage Offset", CPU),
    ("TjMax Offset", CPU),
    ("Package C State Limit", CPU),
    ("Thermal Velocity Boost", CPU),
    ("Enhanced Thermal Velocity Boost", CPU),
    ("Extreme Memory Profile(XMP)", RAM),
    ("Memory Try It!", RAM),
    ("SA GV", RAM),
    ("tMOD", RAM),
    ("tWR", RAM),
    ("tRTP", RAM),
    ("tRFC", RAM),
    ("tRFC4", RAM),
    ("tWTR", RAM),
    ("tWTR_L", RAM),
    ("tWTR_S", RAM),
    ("tCCD", RAM),
    ("tCCD_L", RAM),
    ("tRRD_S", RAM),
]


# (name pattern, help pattern, display name). Applied after classification.
RENAMES: list[tuple[str, str, str]] = [
    (r"^Fast Boot$", r"MRC", "MRC Fast Boot"),  # the memory-training one, not Windows'
]


def canonical_name(question: str) -> str:
    """The setting name without the value MSI pads onto it.

    An MSI export writes "tCL                          40": the name, a run of
    spaces, then the current value. Keeping that would show the stock value in
    the label and stop the two channel copies from grouping once one of them is
    changed, so everything after the first run of two or more spaces is dropped.
    """
    return re.split(r"\s{2,}", question.strip())[0].strip()


def rename(name: str, help_text: str) -> str:
    for name_pattern, help_pattern, display in RENAMES:
        if re.search(name_pattern, name) and (
            not help_pattern or re.search(help_pattern, help_text, re.I)
        ):
            return display
    return name


def classify(name: str, help_text: str) -> str | None | bool:
    """Section title, None to drop, or False when no rule applies."""
    for section, name_pattern, help_pattern in RULES:
        if re.search(name_pattern, name) and (
            not help_pattern or re.search(help_pattern, help_text, re.I)
        ):
            return section
    return False


def load(path: Path) -> tuple[dict, list[dict]]:
    parsed = parse_sce_file(path, source="nvram", commented=False)
    return parsed["metadata"], [setting_to_dict(item) for item in parsed["settings"]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("stock", type=Path)
    parser.add_argument("tuned", type=Path)
    parser.add_argument("--name", required=True, help="e.g. LGA 1700 DDR5")
    parser.add_argument("--platform", required=True, help="e.g. lga1700-ddr5")
    parser.add_argument("--vendor", default="", help="firmware vendor, e.g. MSI, ASUS, ASRock, Gigabyte")
    parser.add_argument("--board", default="", help="board name recorded in the preset")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    stock_meta, stock = load(args.stock)
    tuned_meta, tuned = load(args.tuned)
    tuned_by_id = {
        identity_without_crc(s): s for s in tuned if has_identity(identity_without_crc(s))
    }

    groups: "OrderedDict[tuple[str, str, str, str], dict]" = OrderedDict()
    unplaced: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    placed: set[str] = set()

    def place(section: str, name: str, before: dict, after: dict, key: str) -> None:
        kind = str(before.get("kind"))
        tuned_raw = raw_value(after) or raw_value(before)
        tuned_label = str(after.get("current_label") or "") if kind == "options" else ""
        group = (section, name, kind, tuned_raw)
        entry = groups.get(group)
        if entry is None:
            entry = {
                "name": name,
                "kind": kind,
                "tuned": tuned_raw,
                "tuned_label": tuned_label,
                "help": str(before.get("help") or ""),
                "identities": [],
            }
            groups[group] = entry
        entry["identities"].append(key)
        placed.add(key)

    # First, what the tuned export changed.
    for before in stock:
        key = identity_without_crc(before)
        if not has_identity(key) or key in seen:
            continue
        seen.add(key)
        after = tuned_by_id.get(key)
        if after is None or after.get("kind") != before.get("kind"):
            continue
        if raw_value(before) == raw_value(after):
            continue
        name = canonical_name(str(before.get("question") or ""))
        help_text = str(before.get("help") or "")
        section = classify(name, help_text)
        name = rename(name, help_text)
        label = f"{name}  [{key}]  {raw_value(before)} -> {raw_value(after)}"
        if section is None:
            dropped.append(label)
            continue
        if section is False:
            unplaced.append(label)
            continue
        place(section, name, before, after, key)

    # Then the standing list, for anything the exports agree on.
    extra_sections = dict(EXTRA)
    for before in stock:
        key = identity_without_crc(before)
        if not has_identity(key) or key in placed:
            continue
        name = canonical_name(str(before.get("question") or ""))
        section = extra_sections.get(name)
        if section is None:
            continue
        after = tuned_by_id.get(key) or before
        if after.get("kind") != before.get("kind"):
            continue
        if not (raw_value(after) or raw_value(before)):
            unplaced.append(f"{name}  [{key}]  no readable value; left out")
            continue
        place(section, name, before, after, key)

    # A name that appears with two different tuned values is two controls;
    # tell them apart by the value rather than by an opaque token. When two
    # records also share the label (two "SA GV" switches that both read
    # Disabled) the token is all that is left to tell them apart.
    by_name: dict[tuple[str, str], list[dict]] = {}
    for (section, name, _kind, _raw), entry in groups.items():
        by_name.setdefault((section, name), []).append(entry)
    for entries in by_name.values():
        if len(entries) < 2:
            continue
        labelled = [f"{e['name']} ({e['tuned_label'] or e['tuned']})" for e in entries]
        for entry, candidate in zip(entries, labelled):
            if labelled.count(candidate) > 1:
                candidate = f"{candidate[:-1]}, token {entry['identities'][0].split(':')[0]})"
            entry["name"] = candidate

    sections: "OrderedDict[str, list[dict]]" = OrderedDict(
        (title, []) for title in (CPU, RAM, GPU)
    )
    for (section, _name, _kind, _raw), entry in groups.items():
        sections[section].append(entry)

    preset = {
        "schema_version": SCHEMA_VERSION,
        "name": args.name,
        "platform": args.platform,
        "source": {
            # Identities are a vendor's layout. An ASUS, ASRock, or Gigabyte board
            # will not match a preset built from an MSI export, by design.
            "vendor": args.vendor,
            "board": args.board,
            "stock_hii_crc32": str(stock_meta.get("hii_crc32") or ""),
            "tuned_hii_crc32": str(tuned_meta.get("hii_crc32") or ""),
            "amisce_version": str(tuned_meta.get("amisce_version") or ""),
            "stock_created_on": str(stock_meta.get("created_on") or ""),
            "tuned_created_on": str(tuned_meta.get("created_on") or ""),
        },
        "sections": [
            {"title": title, "settings": entries}
            for title, entries in sections.items()
            if entries
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(preset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    total = sum(len(v) for v in sections.values())
    identities = sum(len(e["identities"]) for v in sections.values() for e in v)
    print(f"wrote {args.out}: {total} controls over {identities} identities")
    for title, entries in sections.items():
        print(f"  {title}: {len(entries)}")
    if dropped:
        print(f"\ndropped as not overclocking controls ({len(dropped)}):")
        for line in dropped:
            print("  -", line)
    if unplaced:
        print(
            f"\nUNPLACED -- changed but no rule matched ({len(unplaced)}); "
            "add a rule or they are left out:"
        )
        for line in unplaced:
            print("  ?", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
