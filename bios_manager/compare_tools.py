from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import BiosManagerError, ValidationError
from .scewin_parser import parse_sce_file, setting_to_dict


@dataclass(frozen=True)
class NvramSnapshot:
    path: Path
    sha256: str
    hii_crc32: str
    amisce_version: str
    settings: list[dict[str, Any]]


@dataclass(frozen=True)
class CompareRow:
    setting: str
    help: str
    stock_value: str
    overclocked_value: str
    token_hex: str
    offset_hex: str
    width_hex: str
    kind: str
    status: str
    changed: bool
    identity: str


@dataclass(frozen=True)
class CompareResult:
    stock: NvramSnapshot
    overclocked: NvramSnapshot
    rows: list[CompareRow]
    hii_match: bool

    @property
    def changed_count(self) -> int:
        return sum(1 for row in self.rows if row.changed)

    @property
    def same_count(self) -> int:
        return sum(1 for row in self.rows if not row.changed)


def load_snapshot(path: str | Path) -> NvramSnapshot:
    source = Path(path).resolve()
    if not source.is_file():
        raise ValidationError(f"NVRAM file not found: {source}")
    try:
        parsed = parse_sce_file(source, source="compare", commented=False)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Could not parse NVRAM file: {exc}") from exc

    metadata = parsed.get("metadata") or {}
    hii_crc32 = str(metadata.get("hii_crc32") or "").upper()
    settings = [setting_to_dict(item) for item in parsed.get("settings") or []]
    if not hii_crc32:
        raise ValidationError("The selected file does not contain HIICrc32.")
    if not settings:
        raise ValidationError("The selected file does not contain SCEWIN setup records.")

    return NvramSnapshot(
        path=source,
        sha256=str(parsed.get("sha256") or ""),
        hii_crc32=hii_crc32,
        amisce_version=str(metadata.get("amisce_version") or "Unknown"),
        settings=settings,
    )


EMPTY_IDENTITY = "::"


def identity_without_crc(setting: dict[str, Any]) -> str:
    return ":".join(
        str(setting.get(key) or "").upper()
        for key in ("token_hex", "offset_hex", "width_hex")
    )


def has_identity(key: str) -> bool:
    """Whether a token/offset/width identity can be matched against another file.

    A record carrying none of the three fields collapses to EMPTY_IDENTITY, which
    cannot identify anything. Every caller has to skip those the same way: when
    the indexes dropped them but the row loops did not, two identical files
    reported a spurious "Only in NVRAM 1" change.
    """
    return bool(key) and key != EMPTY_IDENTITY


def raw_value(setting: dict[str, Any]) -> str:
    if setting.get("kind") == "options":
        return str(setting.get("current_code_hex") or "").upper()
    if setting.get("kind") == "value":
        value = setting.get("current_value")
        return "" if value is None else str(value)
    return ""


def display_value(setting: dict[str, Any] | None) -> str:
    if setting is None:
        return "—"
    if setting.get("kind") == "options":
        label = setting.get("current_label")
        code = setting.get("current_code_hex")
        if label and code:
            return f"{label} [{str(code).upper()}]"
        if code:
            return f"[{str(code).upper()}]"
        return "Unknown"
    if setting.get("kind") == "value":
        value = setting.get("current_value")
        return "Unknown" if value is None else f"<{value}>"
    return "Unknown"


def _unique_index(settings: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    index: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for setting in settings:
        key = identity_without_crc(setting)
        if not has_identity(key):
            continue
        if key in index:
            ambiguous.add(key)
        else:
            index[key] = setting
    for key in ambiguous:
        index.pop(key, None)
    return index, ambiguous


def compare_snapshots(stock: NvramSnapshot, overclocked: NvramSnapshot) -> CompareResult:
    stock_index, stock_ambiguous = _unique_index(stock.settings)
    oc_index, oc_ambiguous = _unique_index(overclocked.settings)
    all_ambiguous = stock_ambiguous | oc_ambiguous

    rows: list[CompareRow] = []
    seen: set[str] = set()

    def make_row(
        key: str,
        stock_setting: dict[str, Any] | None,
        oc_setting: dict[str, Any] | None,
    ) -> CompareRow:
        base = stock_setting or oc_setting or {}
        if key in all_ambiguous:
            status = "Ambiguous identity"
            changed = True
        elif stock_setting is None:
            status = "Only in NVRAM 2"
            changed = True
        elif oc_setting is None:
            status = "Only in NVRAM 1"
            changed = True
        elif stock_setting.get("kind") != oc_setting.get("kind"):
            status = "Type changed"
            changed = True
        elif raw_value(stock_setting) != raw_value(oc_setting):
            status = "Changed"
            changed = True
        else:
            status = "Same"
            changed = False

        # Only value/type/presence differences count as a change. A differing
        # setting name (the question label) is shown for context but never marks
        # a row as changed on its own — matching is by token/offset/width identity.
        stock_name = str((stock_setting or {}).get("question") or "")
        oc_name = str((oc_setting or {}).get("question") or "")
        name = stock_name or oc_name or "N/A"
        if stock_name and oc_name and stock_name != oc_name:
            name = f"{stock_name} → {oc_name}"

        return CompareRow(
            setting=name,
            help=str(base.get("help") or ""),
            stock_value=display_value(stock_setting),
            overclocked_value=display_value(oc_setting),
            token_hex=str(base.get("token_hex") or ""),
            offset_hex=str(base.get("offset_hex") or ""),
            width_hex=str(base.get("width_hex") or ""),
            kind=str(base.get("kind") or "unknown"),
            status=status,
            changed=changed,
            identity=key,
        )

    # Preserve the exact NVRAM 1 order. Entries found only in NVRAM 2
    # are appended in their original NVRAM 2 file order.
    for stock_setting in stock.settings:
        key = identity_without_crc(stock_setting)
        if not has_identity(key) or key in seen:
            continue
        seen.add(key)
        rows.append(make_row(key, stock_setting, oc_index.get(key)))

    for oc_setting in overclocked.settings:
        key = identity_without_crc(oc_setting)
        if not has_identity(key) or key in seen:
            continue
        seen.add(key)
        rows.append(make_row(key, stock_index.get(key), oc_setting))

    return CompareResult(
        stock=stock,
        overclocked=overclocked,
        rows=rows,
        hii_match=stock.hii_crc32 == overclocked.hii_crc32,
    )


def compare_files(stock_path: str | Path, overclocked_path: str | Path) -> CompareResult:
    return compare_snapshots(load_snapshot(stock_path), load_snapshot(overclocked_path))


def export_compare_csv(path: str | Path, result: CompareResult, changed_only: bool = False) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [row for row in result.rows if row.changed or not changed_only]
    try:
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "Setting",
                    "Help String",
                    "NVRAM 1 Value",
                    "NVRAM 2 Value",
                    "Token",
                    "Offset",
                    "Width",
                    "Type",
                    "Status",
                    "Identity",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row.setting,
                        row.help,
                        row.stock_value,
                        row.overclocked_value,
                        row.token_hex,
                        row.offset_hex,
                        row.width_hex,
                        row.kind,
                        row.status,
                        row.identity,
                    ]
                )
    except OSError as exc:
        raise BiosManagerError(f"Could not export comparison CSV: {exc}") from exc
    return output
