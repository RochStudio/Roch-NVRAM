#!/usr/bin/env python3
"""
Parse AMI SCEWIN/AMISCE nvram.txt exports without executing SCEWIN.

Usage:
    python scewin_parser.py nvram.txt \
        --dupes Dupes.txt \
        --label "Z890 Tachyon / 270K" \
        --output parsed.json \
        --summary summary.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


OPTION_RE = re.compile(
    r"^\s*(?P<selected>\*)?\[(?P<code>[0-9A-Fa-f]+)\]"
    r"(?P<label>.*?)(?:\s*//.*)?$"
)
ENUM_RE = re.compile(r"^\[(?P<code>[0-9A-Fa-f]+)\](?P<label>.*)$")
VALUE_RE = re.compile(r"^<(?P<value>[^>]*)>")

OPTIONS_PREFIX = "Options\t="
FIELD_PREFIXES = (
    "Setup Question\t=",
    "Help String\t=",
    "Token\t=",
    "Offset\t=",
    "Width\t=",
    "BIOS Default\t=",
    "Value\t=",
)


def option_line_indexes(lines: list[str]) -> list[int]:
    """Indexes of the lines that make up a record's Options block.

    The reader (parse_record) and the writer (core._modify_block) must agree on
    this exactly. When the writer scanned fewer lines than the reader listed it
    left a stale '*' behind and produced an import record with two selected
    options, so the rule lives here once and both call it.

    A row belongs to the block from the "Options\t=" line until the first blank
    line or the first line that starts another record field. Indentation is not
    part of the rule: AMISCE indents continuation rows, but a row at column 0 is
    still a row.
    """
    indexes: list[int] = []
    in_options = False
    for index, line in enumerate(lines):
        if line.startswith(OPTIONS_PREFIX):
            in_options = True
            indexes.append(index)
        elif any(line.startswith(prefix) for prefix in FIELD_PREFIXES):
            in_options = False
        elif not in_options:
            continue
        elif not line.strip():
            in_options = False
        else:
            indexes.append(index)
    return indexes


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sce_text(path: Path) -> str:
    # SCEWIN exports are commonly ANSI/Latin-1 with mixed CRLF/CR line endings.
    raw = path.read_bytes()
    return raw.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")


@dataclass
class BiosOption:
    code_hex: str
    code: int
    label: str
    selected: bool = False


@dataclass
class BiosSetting:
    source: str
    question: str = ""
    help: str = ""
    token_hex: str = ""
    token: int | None = None
    offset_hex: str = ""
    offset: int | None = None
    width_hex: str = ""
    width: int | None = None
    kind: str = "unknown"

    bios_default_raw: str | None = None
    bios_default_type: str | None = None
    bios_default_code_hex: str | None = None
    bios_default_label: str | None = None
    bios_default_value: str | None = None

    current_value: str | None = None
    current_code_hex: str | None = None
    current_code: int | None = None
    current_label: str | None = None

    options: list[BiosOption] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    export_id: str = ""

    def finalize(self, hii_crc32: str | None) -> None:
        crc = hii_crc32 or "NOCRC"
        self.export_id = (
            f"{crc}:{self.token_hex or 'NOTOKEN'}:"
            f"{self.offset_hex or 'NOOFFSET'}:{self.width_hex or 'NOWIDTH'}"
        )

        if not self.bios_default_raw:
            self.warnings.append("missing_bios_default")

        if self.kind == "options":
            selected = [option for option in self.options if option.selected]
            selected_codes = {option.code_hex for option in selected}

            if not selected:
                self.warnings.append("no_selected_option")
            elif len(selected_codes) == 1:
                chosen = selected[0]
                self.current_code_hex = chosen.code_hex
                self.current_code = chosen.code
                self.current_label = chosen.label
                if len(selected) > 1:
                    self.warnings.append("duplicate_selected_option_rows")
            else:
                self.warnings.append("multiple_selected_option_codes")

            option_keys = [(option.code_hex, option.label) for option in self.options]
            if len(option_keys) != len(set(option_keys)):
                self.warnings.append("duplicate_option_rows")
            if any(not option.label for option in self.options):
                self.warnings.append("empty_option_label")

        if self.question == "N/A":
            self.warnings.append("question_name_na")


def parse_metadata(text: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    patterns = {
        "script_file_name": r"// Script File Name\s*:\s*(.+)",
        "created_on": r"// Created on\s+(.+?)\s*$",
        "amisce_version": r"// AMISCE Utility\. Ver\s+([^\s]+)",
        "hii_crc32": r"HIICrc32=\s*([0-9A-Fa-f]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            metadata[key] = match.group(1).strip()
    return metadata


def normalize_commented_dupes(text: str) -> str:
    normalized: list[str] = []
    for line in text.splitlines():
        if line.startswith("// "):
            normalized.append(line[3:])
        elif line == "//":
            normalized.append("")
        else:
            normalized.append(line)
    return "\n".join(normalized)


def split_records(text: str) -> list[list[str]]:
    lines = text.splitlines()
    starts = [
        index for index, line in enumerate(lines)
        if line.startswith("Setup Question\t=")
    ]
    records: list[list[str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        records.append(lines[start:end])
    return records


def parse_hex(value: str) -> int | None:
    try:
        return int(value, 16)
    except ValueError:
        return None


def parse_record(
    block: list[str],
    source: str,
    hii_crc32: str | None,
) -> BiosSetting:
    setting = BiosSetting(source=source)
    # Option-block membership is decided by the shared rule so the reader and the
    # writer in core._modify_block can never disagree about which rows are options.
    option_lines = set(option_line_indexes(block))

    for index, line in enumerate(block):
        if index in option_lines:
            header = line.startswith(OPTIONS_PREFIX)
            if header:
                setting.kind = "options"
            match = OPTION_RE.match(line.split("\t=", 1)[1] if header else line)
            if match:
                setting.options.append(
                    BiosOption(
                        code_hex=match.group("code").upper(),
                        code=int(match.group("code"), 16),
                        label=match.group("label").strip(),
                        selected=bool(match.group("selected")),
                    )
                )
            else:
                setting.warnings.append(
                    "unparsed_options_header" if header else "unparsed_option_line"
                )

        elif line.startswith("Setup Question\t="):
            setting.question = line.split("\t=", 1)[1].strip()

        elif line.startswith("Help String\t="):
            setting.help = line.split("\t=", 1)[1].strip()

        elif line.startswith("Token\t="):
            value = line.split("\t=", 1)[1].split("//", 1)[0].strip().upper()
            setting.token_hex = value
            setting.token = parse_hex(value)

        elif line.startswith("Offset\t="):
            value = line.split("\t=", 1)[1].strip().upper()
            setting.offset_hex = value
            setting.offset = parse_hex(value)

        elif line.startswith("Width\t="):
            value = line.split("\t=", 1)[1].strip().upper()
            setting.width_hex = value
            setting.width = parse_hex(value)

        elif line.startswith("BIOS Default\t="):
            value = line.split("\t=", 1)[1].strip()
            setting.bios_default_raw = value

            enum_match = ENUM_RE.match(value)
            value_match = VALUE_RE.match(value)
            if enum_match:
                setting.bios_default_type = "enum"
                setting.bios_default_code_hex = enum_match.group("code").upper()
                setting.bios_default_label = enum_match.group("label").strip()
            elif value_match:
                setting.bios_default_type = "value"
                setting.bios_default_value = value_match.group("value")
            else:
                setting.bios_default_type = "other"

        elif line.startswith("Value\t="):
            setting.kind = "value"
            value = line.split("\t=", 1)[1].strip()
            match = VALUE_RE.match(value)
            setting.current_value = match.group("value") if match else value

    if setting.kind == "unknown":
        setting.warnings.append("missing_value_or_options")

    setting.finalize(hii_crc32)
    return setting


def parse_sce_file(path: Path, source: str, commented: bool = False) -> dict[str, Any]:
    text = read_sce_text(path)
    metadata = parse_metadata(text)
    parse_text = normalize_commented_dupes(text) if commented else text
    settings = [
        parse_record(block, source=source, hii_crc32=metadata.get("hii_crc32"))
        for block in split_records(parse_text)
    ]
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "metadata": metadata,
        "settings": settings,
    }


def make_summary(
    active: list[BiosSetting],
    dupes: list[BiosSetting],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    question_groups: dict[str, list[BiosSetting]] = defaultdict(list)
    offset_width_groups: dict[tuple[str, str], list[BiosSetting]] = defaultdict(list)

    for setting in active:
        question_groups[setting.question].append(setting)
        offset_width_groups[(setting.offset_hex, setting.width_hex)].append(setting)

    warning_counts = Counter(
        warning for setting in active for warning in setting.warnings
    )

    return {
        "metadata": metadata,
        "active_setting_count": len(active),
        "excluded_duplicate_setting_count": len(dupes),
        "kind_counts": dict(Counter(setting.kind for setting in active)),
        "width_byte_counts": {
            str(width): count
            for width, count in sorted(
                Counter(setting.width for setting in active).items(),
                key=lambda item: (-1 if item[0] is None else item[0]),
            )
        },
        "unique_display_name_count": len(question_groups),
        "duplicate_display_name_group_count": sum(
            1 for group in question_groups.values() if len(group) > 1
        ),
        "settings_in_duplicate_display_name_groups": sum(
            len(group) for group in question_groups.values() if len(group) > 1
        ),
        "duplicate_offset_width_group_count": sum(
            1 for group in offset_width_groups.values() if len(group) > 1
        ),
        "settings_in_duplicate_offset_width_groups": sum(
            len(group) for group in offset_width_groups.values() if len(group) > 1
        ),
        "warning_counts": dict(warning_counts),
        "identity_note": (
            "export_id uses HII CRC + token + offset + width. It is suitable for "
            "matching this exact SCEWIN export family, but it is not a substitute "
            "for a UEFI variable-store GUID and must be invalidated when the HII "
            "CRC or BIOS version changes."
        ),
    }


def setting_to_dict(setting: BiosSetting) -> dict[str, Any]:
    return asdict(setting)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("nvram", type=Path)
    parser.add_argument("--dupes", type=Path)
    parser.add_argument("--label", default="")
    parser.add_argument("--output", type=Path, default=Path("parsed.json"))
    parser.add_argument("--summary", type=Path, default=Path("summary.json"))
    args = parser.parse_args()

    active_result = parse_sce_file(args.nvram, source="nvram", commented=False)
    dupes_result = (
        parse_sce_file(args.dupes, source="dupes", commented=True)
        if args.dupes
        else {"settings": [], "metadata": {}, "sha256": None}
    )

    active = active_result["settings"]
    dupes = dupes_result["settings"]
    metadata = active_result["metadata"]

    output = {
        "schema_version": 1,
        "label": args.label,
        "source": {
            "nvram_path": str(args.nvram),
            "nvram_sha256": active_result["sha256"],
            "dupes_path": str(args.dupes) if args.dupes else None,
            "dupes_sha256": dupes_result.get("sha256"),
        },
        "metadata": metadata,
        "active_settings": [setting_to_dict(setting) for setting in active],
        "excluded_duplicates": [setting_to_dict(setting) for setting in dupes],
    }
    summary = make_summary(active, dupes, metadata)
    summary["label"] = args.label
    summary["source"] = output["source"]

    args.output.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    args.summary.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
