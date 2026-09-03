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

"""Quick Settings presets: the overclocking-relevant knobs of a platform.

A preset is a JSON file under assets/quick_settings/. It is generated from a
stock export and a tuned export of the same board by tools/make_quick_settings.py:
every setting whose value differs between the two, sorted into CPU, RAM, and
GPU sections, with the tuned value recorded as the suggested one.

A preset never names a setting by its HII CRC. Each entry carries one or more
token/offset/width identities -- the same key Compare and Load NVRAM match on --
so it applies to any export of that firmware family, and an entry with several
identities writes the same value to each. That is how the per-channel copies of
a DDR5 timing (tCL for channel A and channel B) become a single control.

This module is Qt-free. resolve() matches a preset against a loaded profile and
changes_for() turns one control's value into ordinary PendingChange objects, so
whatever the Quick Settings tab queues goes through the same preflight, backup,
and verification as a change queued from the NVRAM table.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compare_tools import has_identity, identity_without_crc
from .core import (
    BiosManagerError,
    ParsedProfile,
    PendingChange,
    ScewinDocument,
    ValidationError,
    raw_value,
)

SCHEMA_VERSION = 1
PRESET_SUFFIX = ".json"


def asset_root() -> Path:
    """The assets/ directory, both from source and inside a PyInstaller build."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parents[1]
    return base / "assets"


def preset_dir() -> Path:
    return asset_root() / "quick_settings"


@dataclass(frozen=True)
class QuickSetting:
    name: str
    kind: str  # "options" or "value"
    tuned: str  # raw value as SCEWIN writes it: an option code or a value text
    tuned_label: str  # what the tuned option is called; "" for values
    help: str
    identities: tuple[str, ...]  # token:offset:width, as identity_without_crc()


@dataclass(frozen=True)
class QuickSection:
    title: str
    settings: tuple[QuickSetting, ...]


@dataclass(frozen=True)
class QuickPreset:
    name: str
    platform: str
    source: dict[str, str]
    sections: tuple[QuickSection, ...]
    path: Path

    @property
    def setting_count(self) -> int:
        return sum(len(section.settings) for section in self.sections)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(f"Quick Settings preset: {message}")


def _parse_setting(data: Any, where: str) -> QuickSetting:
    _require(isinstance(data, dict), f"{where} is not an object")
    name = str(data.get("name") or "").strip()
    _require(bool(name), f"{where} has no name")
    kind = str(data.get("kind") or "")
    _require(kind in ("options", "value"), f"{name!r} has kind {kind!r}, expected options or value")
    tuned = str(data.get("tuned") if data.get("tuned") is not None else "")
    _require(tuned != "", f"{name!r} has no tuned value")
    identities = tuple(str(item).upper() for item in data.get("identities") or [])
    _require(bool(identities), f"{name!r} has no identities")
    for identity in identities:
        _require(
            has_identity(identity) and identity.count(":") == 2,
            f"{name!r} has a malformed identity {identity!r}",
        )
    if kind == "options":
        tuned = tuned.upper()
    return QuickSetting(
        name=name,
        kind=kind,
        tuned=tuned,
        tuned_label=str(data.get("tuned_label") or ""),
        help=str(data.get("help") or ""),
        identities=identities,
    )


def load_preset(path: str | Path) -> QuickPreset:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Could not read Quick Settings preset {source.name}: {exc}") from exc
    _require(isinstance(data, dict), "top level is not an object")
    _require(
        data.get("schema_version") == SCHEMA_VERSION,
        f"{source.name} has schema_version {data.get('schema_version')!r}, expected {SCHEMA_VERSION}",
    )
    name = str(data.get("name") or source.stem)
    sections: list[QuickSection] = []
    seen: set[str] = set()
    for index, raw_section in enumerate(data.get("sections") or []):
        _require(isinstance(raw_section, dict), f"section {index} is not an object")
        title = str(raw_section.get("title") or "").strip()
        _require(bool(title), f"section {index} has no title")
        settings = tuple(
            _parse_setting(item, f"{title} setting {position}")
            for position, item in enumerate(raw_section.get("settings") or [])
        )
        for setting in settings:
            for identity in setting.identities:
                _require(identity not in seen, f"identity {identity} appears twice ({setting.name!r})")
                seen.add(identity)
        sections.append(QuickSection(title=title, settings=settings))
    _require(bool(sections), f"{source.name} has no sections")
    raw_source = data.get("source") or {}
    return QuickPreset(
        name=name,
        platform=str(data.get("platform") or ""),
        source=(
            {str(key): str(value) for key, value in raw_source.items()}
            if isinstance(raw_source, dict)
            else {}
        ),
        sections=tuple(sections),
        path=source,
    )


def load_presets(directory: str | Path | None = None) -> list[QuickPreset]:
    """Every preset in the directory, by name. A broken file is skipped, not fatal."""
    folder = Path(directory) if directory is not None else preset_dir()
    if not folder.is_dir():
        return []
    presets: list[QuickPreset] = []
    for path in sorted(folder.glob(f"*{PRESET_SUFFIX}")):
        try:
            presets.append(load_preset(path))
        except BiosManagerError:
            continue
    presets.sort(key=lambda preset: preset.name.casefold())
    return presets


@dataclass(frozen=True)
class ResolvedSetting:
    """One preset control matched against the loaded NVRAM."""

    spec: QuickSetting
    targets: tuple[dict[str, Any], ...]  # live settings this control writes
    missing: tuple[str, ...]  # identities the loaded export does not have
    blocked: tuple[str, ...]  # why a matched setting cannot be written

    @property
    def available(self) -> bool:
        return bool(self.targets) and not self.blocked

    @property
    def current_raw(self) -> tuple[str, ...]:
        return tuple(raw_value(target) for target in self.targets)

    @property
    def matches_tuned(self) -> bool:
        return bool(self.targets) and all(raw == self.spec.tuned for raw in self.current_raw)

    def current_display(self) -> str:
        if not self.targets:
            return "blocked" if self.blocked else "not on this board"
        shown: list[str] = []
        for target in self.targets:
            text = ScewinDocument.setting_display(target)
            if text not in shown:
                shown.append(text)
        return " / ".join(shown)


def placeholders(preset: QuickPreset) -> list[tuple[QuickSection, list[ResolvedSetting]]]:
    """The preset laid out with nothing loaded: every entry present, none matched."""
    return [
        (
            section,
            [ResolvedSetting(spec, (), spec.identities, ()) for spec in section.settings],
        )
        for section in preset.sections
    ]


def resolve(
    preset: QuickPreset, profile: ParsedProfile, document: ScewinDocument
) -> list[tuple[QuickSection, list[ResolvedSetting]]]:
    """Match every preset entry to the loaded export by token/offset/width."""
    by_identity: dict[str, dict[str, Any]] = {}
    for setting in profile.settings:
        key = identity_without_crc(setting)
        if has_identity(key) and key not in by_identity:
            by_identity[key] = setting

    resolved: list[tuple[QuickSection, list[ResolvedSetting]]] = []
    for section in preset.sections:
        rows: list[ResolvedSetting] = []
        for spec in section.settings:
            targets: list[dict[str, Any]] = []
            missing: list[str] = []
            blocked: list[str] = []
            for identity in spec.identities:
                live = by_identity.get(identity)
                if live is None:
                    missing.append(identity)
                    continue
                if str(live.get("kind")) != spec.kind:
                    blocked.append(
                        f"{identity}: live setting is {live.get('kind')}, preset expects {spec.kind}"
                    )
                    continue
                editable, reason = document.is_editable(live)
                if not editable:
                    blocked.append(f"{identity}: {reason}")
                    continue
                targets.append(live)
            rows.append(ResolvedSetting(spec, tuple(targets), tuple(missing), tuple(blocked)))
        resolved.append((section, rows))
    return resolved


def changes_for(
    resolved: ResolvedSetting,
    raw: str,
    profile: ParsedProfile,
    document: ScewinDocument,
) -> tuple[list[PendingChange], list[str], list[str]]:
    """Turn one control's value into pending changes, one per live target.

    Returns (changes, reset_ids, errors). reset_ids are targets whose live value
    already equals the requested one: nothing to write, and any change queued
    for them earlier should be dropped, the way the edit dialog does it.
    """
    changes: list[PendingChange] = []
    reset: list[str] = []
    errors: list[str] = []
    wanted = raw.upper() if resolved.spec.kind == "options" else raw
    for target in resolved.targets:
        export_id = str(target.get("export_id") or "")
        if wanted == raw_value(target):
            reset.append(export_id)
            continue
        try:
            changes.append(document.build_change(profile, export_id, wanted))
        except BiosManagerError as exc:
            errors.append(f"{resolved.spec.name} ({identity_without_crc(target)}): {exc}")
    return changes, reset, errors
