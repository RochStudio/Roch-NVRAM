from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

from .scewin_parser import option_line_indexes


HII_RE = re.compile(r"^HIICrc32=\s*([0-9A-Fa-f]+)\s*$", re.MULTILINE)
FIELD_RE = {
    "token": re.compile(r"^Token\t=\s*([0-9A-Fa-f]+)", re.MULTILINE),
    "offset": re.compile(r"^Offset\t=\s*([0-9A-Fa-f]+)\s*$", re.MULTILINE),
    "width": re.compile(r"^Width\t=\s*([0-9A-Fa-f]+)\s*$", re.MULTILINE),
}
# The prefix is \s* (not \s+) to match scewin_parser.OPTION_RE: AMISCE indents
# continuation rows, but a row at column 0 is still a row.
OPTION_LINE_RE = re.compile(
    r"^(?P<prefix>Options\t=|\s*)(?P<star>\*)?"
    r"\[(?P<code>[0-9A-Fa-f]+)\](?P<tail>.*)$"
)
VALUE_LINE_RE = re.compile(r"^(?P<prefix>Value\t=\s*)<(?P<value>[^>]*)>(?P<tail>.*)$")


class BiosManagerError(RuntimeError):
    """Base error for safe, user-displayable failures."""


class ProfileMismatchError(BiosManagerError):
    pass


class ValidationError(BiosManagerError):
    pass


@dataclass(frozen=True)
class PendingChange:
    export_id: str
    question: str
    kind: str
    old_display: str
    new_display: str
    new_raw: str
    token_hex: str
    offset_hex: str
    width_hex: str
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecordSpan:
    export_id: str
    start: int
    end: int
    token_hex: str
    offset_hex: str
    width_hex: str


@dataclass
class ParsedProfile:
    path: Path
    label: str
    metadata: dict[str, Any]
    settings: list[dict[str, Any]]
    source: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "ParsedProfile":
        profile_path = Path(path)
        # Callers only handle BiosManagerError, so a missing file or malformed
        # JSON has to arrive as one rather than as OSError/ValueError.
        try:
            with profile_path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except OSError as exc:
            raise ValidationError(f"Could not read the parsed profile: {exc}") from exc
        except ValueError as exc:
            raise ValidationError(f"The parsed profile is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValidationError("The parsed profile is not a SCEWIN profile object.")
        if data.get("schema_version") != 1:
            raise ValidationError("Unsupported parsed profile schema.")
        settings = data.get("active_settings")
        if not isinstance(settings, list):
            raise ValidationError("Profile does not contain active_settings.")
        return cls(
            path=profile_path,
            label=str(data.get("label") or profile_path.stem),
            metadata=dict(data.get("metadata") or {}),
            settings=settings,
            source=dict(data.get("source") or {}),
        )

    @property
    def hii_crc32(self) -> str:
        return str(self.metadata.get("hii_crc32") or "").upper()

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {str(setting["export_id"]): setting for setting in self.settings}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_latin1_normalized(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    return raw.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")


class ScewinDocument:
    """An immutable source export plus safe functions that create modified copies."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self.text = read_latin1_normalized(self.path)
            self.source_sha256 = sha256_file(self.path)
        except OSError as exc:
            raise ValidationError(f"Could not read the SCEWIN export: {exc}") from exc
        hii_match = HII_RE.search(self.text)
        if not hii_match:
            raise ValidationError("The SCEWIN file does not contain HIICrc32.")
        self.hii_crc32 = hii_match.group(1).upper()
        self.records = self._index_records()

    def _index_records(self) -> dict[str, RecordSpan]:
        starts = [match.start() for match in re.finditer(r"^Setup Question\t=", self.text, re.MULTILINE)]
        records: dict[str, RecordSpan] = {}
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(self.text)
            block = self.text[start:end]
            values: dict[str, str] = {}
            for name, pattern in FIELD_RE.items():
                match = pattern.search(block)
                if match:
                    values[name] = match.group(1).upper()
            if len(values) != 3:
                continue
            export_id = (
                f"{self.hii_crc32}:{values['token']}:"
                f"{values['offset']}:{values['width']}"
            )
            if export_id in records:
                raise ValidationError(f"Duplicate export identity in source: {export_id}")
            records[export_id] = RecordSpan(
                export_id=export_id,
                start=start,
                end=end,
                token_hex=values["token"],
                offset_hex=values["offset"],
                width_hex=values["width"],
            )
        return records

    def verify_profile(self, profile: ParsedProfile) -> None:
        if profile.hii_crc32 != self.hii_crc32:
            raise ProfileMismatchError(
                f"HII CRC mismatch: export is {self.hii_crc32}, "
                f"profile is {profile.hii_crc32 or 'missing'}."
            )
        missing = [setting["export_id"] for setting in profile.settings if setting["export_id"] not in self.records]
        if missing:
            raise ProfileMismatchError(
                f"{len(missing)} profile settings are missing from the source export."
            )

    @staticmethod
    def setting_display(setting: dict[str, Any]) -> str:
        if setting.get("kind") == "options":
            return str(setting.get("current_label") or "Unknown")
        value = setting.get("current_value")
        return "Unknown" if value is None else str(value)

    @staticmethod
    def is_editable(setting: dict[str, Any]) -> tuple[bool, str]:
        warnings = set(setting.get("warnings") or [])
        blocking = {
            "question_name_na",
            "missing_value_or_options",
            "unparsed_options_header",
            "unparsed_option_line",
            "multiple_selected_option_codes",
            "empty_option_label",
            "no_selected_option",
        }
        hit = sorted(warnings & blocking)
        if hit:
            return False, ", ".join(hit)
        if setting.get("kind") == "options":
            options = setting.get("options") or []
            codes = [str(option.get("code_hex") or "").upper() for option in options]
            labels = [str(option.get("label") or "") for option in options]
            if not options or len(codes) != len(set(codes)) or any(not label for label in labels):
                return False, "ambiguous option table"
        elif setting.get("kind") != "value":
            return False, "unknown setting type"
        return True, ""

    @staticmethod
    def normalize_value(value: str, width: int | None) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValidationError("Value cannot be empty.")
        if any(character in cleaned for character in "<>\r\n"):
            raise ValidationError("Value contains unsupported characters.")
        try:
            cleaned.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValidationError(
                "Value contains characters that a SCEWIN export cannot store. "
                "Use plain Latin-1 text."
            ) from exc

        # Small widths are overwhelmingly numeric SCEWIN fields. Accept decimal or 0x.
        if width is not None and width <= 8:
            try:
                number = int(cleaned, 0)
            except ValueError as exc:
                raise ValidationError("Enter a decimal number or a 0x-prefixed hexadecimal number.") from exc
            if number < 0:
                raise ValidationError("Negative values are not supported by this editor.")
            maximum = (1 << (width * 8)) - 1
            if number > maximum:
                raise ValidationError(f"Value exceeds the unsigned {width}-byte maximum ({maximum}).")
            return str(number)
        return cleaned

    def build_change(
        self,
        profile: ParsedProfile,
        export_id: str,
        new_raw: str,
    ) -> PendingChange:
        setting = profile.by_id().get(export_id)
        if setting is None:
            raise ValidationError("The selected setting is not in the loaded profile.")
        editable, reason = self.is_editable(setting)
        if not editable:
            raise ValidationError(f"This setting is blocked from editing: {reason}.")

        old_display = self.setting_display(setting)
        kind = str(setting.get("kind"))
        if kind == "options":
            normalized = new_raw.upper()
            matching = [
                option for option in setting.get("options", [])
                if str(option.get("code_hex") or "").upper() == normalized
            ]
            if len(matching) != 1:
                raise ValidationError("The selected option code is not unique in this setting.")
            new_display = str(matching[0]["label"])
        else:
            normalized = self.normalize_value(new_raw, setting.get("width"))
            new_display = normalized

        return PendingChange(
            export_id=export_id,
            question=str(setting.get("question") or "N/A"),
            kind=kind,
            old_display=old_display,
            new_display=new_display,
            new_raw=normalized,
            token_hex=str(setting.get("token_hex") or ""),
            offset_hex=str(setting.get("offset_hex") or ""),
            width_hex=str(setting.get("width_hex") or ""),
            help=str(setting.get("help") or ""),
        )

    def _modify_block(self, block: str, change: PendingChange) -> str:
        lines = block.split("\n")
        if change.kind == "value":
            changed = 0
            for index, line in enumerate(lines):
                match = VALUE_LINE_RE.match(line)
                if match:
                    lines[index] = f"{match.group('prefix')}<{change.new_raw}>{match.group('tail')}"
                    changed += 1
            if changed != 1:
                raise ValidationError(
                    f"Expected one Value line for {change.export_id}; found {changed}."
                )
            return "\n".join(lines)

        target_hits = 0
        option_rows = 0
        # Same membership rule as the parser. Scanning fewer rows than the parser
        # listed would leave a stale "*" behind and write a record with two
        # selected options, which SCEWIN would import as an ambiguous question.
        for index in option_line_indexes(lines):
            line = lines[index]
            match = OPTION_LINE_RE.match(line)
            if match is None:
                # The parser records unparsed_option_line for these and
                # is_editable refuses the setting, so this is unreachable unless
                # the record changed underneath us. Refuse rather than write a
                # partially normalised block.
                raise ValidationError(
                    f"Unrecognised option row for {change.export_id}: {line!r}."
                )
            option_rows += 1
            code = match.group("code").upper()
            star = "*" if code == change.new_raw.upper() else ""
            if star:
                target_hits += 1
            lines[index] = (
                f"{match.group('prefix')}{star}[{match.group('code')}]"
                f"{match.group('tail')}"
            )

        if option_rows == 0:
            raise ValidationError(f"No option rows found for {change.export_id}.")
        if target_hits != 1:
            raise ValidationError(
                f"Expected one matching option code for {change.export_id}; found {target_hits}."
            )
        return "\n".join(lines)

    def render(self, changes: Iterable[PendingChange]) -> str:
        change_map = {change.export_id: change for change in changes}
        unknown = sorted(set(change_map) - set(self.records))
        if unknown:
            raise ValidationError(f"Changes reference {len(unknown)} unknown source settings.")

        pieces: list[str] = []
        cursor = 0
        for span in sorted(self.records.values(), key=lambda item: item.start):
            change = change_map.get(span.export_id)
            if change is None:
                continue
            pieces.append(self.text[cursor:span.start])
            pieces.append(self._modify_block(self.text[span.start:span.end], change))
            cursor = span.end
        pieces.append(self.text[cursor:])
        return "".join(pieces)

    def write_modified_copy(
        self,
        output_path: str | Path,
        changes: Iterable[PendingChange],
    ) -> Path:
        output = Path(output_path)
        if output.resolve() == self.path.resolve():
            raise ValidationError("The original SCEWIN export cannot be overwritten.")
        rendered = self.render(changes)
        # AMISCE exports use a legacy encoding; CRLF is safest for Windows tools.
        try:
            encoded = rendered.replace("\n", "\r\n").encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValidationError(
                "The modified export contains a character that cannot be written in "
                f"the SCEWIN Latin-1 encoding: {exc.object[exc.start:exc.end]!r}."
            ) from exc
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(encoded)
        except OSError as exc:
            raise BiosManagerError(f"Could not write {output}: {exc}") from exc
        return output


def write_transaction(
    output_path: str | Path,
    profile: ParsedProfile,
    document: ScewinDocument,
    changes: Iterable[PendingChange],
) -> Path:
    output = Path(output_path)
    try:
        profile_sha256 = sha256_file(profile.path)
    except OSError as exc:
        raise BiosManagerError(f"Could not read the profile: {exc}") from exc
    payload = {
        "schema_version": 1,
        "dry_run_only": True,
        "profile_label": profile.label,
        "hii_crc32": document.hii_crc32,
        "source_nvram": str(document.path),
        "source_sha256": document.source_sha256,
        "profile_path": str(profile.path),
        "profile_sha256": profile_sha256,
        "changes": [change.to_dict() for change in changes],
    }
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        raise BiosManagerError(f"Could not write {output}: {exc}") from exc
    return output
