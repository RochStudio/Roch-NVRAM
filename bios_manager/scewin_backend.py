from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .core import (
    BiosManagerError,
    ParsedProfile,
    PendingChange,
    ProfileMismatchError,
    ScewinDocument,
    ValidationError,
    sha256_file,
)
from .scewin_parser import parse_sce_file, setting_to_dict


class ScewinExecutionError(BiosManagerError):
    """SCEWIN could not export, import, or verify the requested transaction."""


@dataclass
class ScewinRunResult:
    returncode: int
    output: str
    log_path: Path


@dataclass
class LiveExport:
    profile: ParsedProfile
    document: ScewinDocument
    profile_path: Path
    nvram_path: Path
    dupes_path: Path | None
    run_result: ScewinRunResult


@dataclass
class ApplyPlan:
    live_export: LiveExport
    changes: list[PendingChange]


@dataclass
class ApplyResult:
    backup_dir: Path
    verified_export: LiveExport
    import_result: ScewinRunResult
    verified_count: int


def is_windows_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _setting_raw(setting: dict) -> str:
    if setting.get("kind") == "options":
        return str(setting.get("current_code_hex") or "").upper()
    return str(setting.get("current_value") if setting.get("current_value") is not None else "")


def write_live_profile(
    nvram_path: Path,
    dupes_path: Path | None,
    output_path: Path,
    label: str = "NVRAM",
) -> Path:
    active_result = parse_sce_file(nvram_path, source="nvram", commented=False)
    if dupes_path and dupes_path.exists():
        dupes_result = parse_sce_file(dupes_path, source="dupes", commented=True)
    else:
        dupes_result = {"settings": [], "sha256": None}

    payload = {
        "schema_version": 1,
        "label": label,
        "source": {
            "nvram_path": str(nvram_path),
            "nvram_sha256": active_result["sha256"],
            "dupes_path": str(dupes_path) if dupes_path and dupes_path.exists() else None,
            "dupes_sha256": dupes_result.get("sha256"),
        },
        "metadata": active_result["metadata"],
        "active_settings": [setting_to_dict(setting) for setting in active_result["settings"]],
        "excluded_duplicates": [setting_to_dict(setting) for setting in dupes_result["settings"]],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def remap_changes(
    old_profile: ParsedProfile,
    fresh_profile: ParsedProfile,
    fresh_document: ScewinDocument,
    changes: Iterable[PendingChange],
) -> list[PendingChange]:
    queued_changes = list(changes)
    if old_profile.hii_crc32 != fresh_profile.hii_crc32:
        raise ProfileMismatchError(
            f"The live HII CRC changed from {old_profile.hii_crc32} "
            f"to {fresh_profile.hii_crc32}. Nothing was imported."
        )

    old_by_id = old_profile.by_id()
    fresh_by_id = fresh_profile.by_id()
    remapped: list[PendingChange] = []
    conflicts: list[str] = []

    for queued in queued_changes:
        old_setting = old_by_id.get(queued.export_id)
        fresh_setting = fresh_by_id.get(queued.export_id)
        if old_setting is None or fresh_setting is None:
            conflicts.append(f"{queued.question}: setting is missing from the fresh export")
            continue
        if old_setting.get("kind") != fresh_setting.get("kind"):
            conflicts.append(f"{queued.question}: setting type changed")
            continue
        if _setting_raw(old_setting) != _setting_raw(fresh_setting):
            conflicts.append(
                f"{queued.question}: live value changed from "
                f"{ScewinDocument.setting_display(old_setting)} to "
                f"{ScewinDocument.setting_display(fresh_setting)}"
            )
            continue
        try:
            remapped.append(
                fresh_document.build_change(fresh_profile, queued.export_id, queued.new_raw)
            )
        except BiosManagerError as exc:
            conflicts.append(f"{queued.question}: {exc}")

    if conflicts or len(remapped) != len(queued_changes):
        detail = "\n".join(conflicts[:20])
        if len(conflicts) > 20:
            detail += f"\n…and {len(conflicts) - 20} more"
        raise ValidationError(
            "The queued changes no longer match the live NVRAM export. "
            "Nothing was imported.\n\n" + detail
        )
    return remapped


def verify_changes(profile: ParsedProfile, changes: Iterable[PendingChange]) -> list[str]:
    by_id = profile.by_id()
    mismatches: list[str] = []
    for change in changes:
        setting = by_id.get(change.export_id)
        if setting is None:
            mismatches.append(f"{change.question}: missing from verification export")
            continue
        actual = _setting_raw(setting)
        expected = change.new_raw.upper() if change.kind == "options" else change.new_raw
        if change.kind == "options":
            actual = actual.upper()
        if actual != expected:
            mismatches.append(
                f"{change.question}: expected {change.new_display}, "
                f"but live export reports {ScewinDocument.setting_display(setting)}"
            )
    return mismatches


class ScewinBackend:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.asset_dir = self.project_root / "scewin"

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            app_root = Path(local_app_data) / "NVRAM"
        else:
            app_root = Path.home() / ".nvram"
        self.app_root = app_root
        self.runtime_dir = app_root / "runtime"
        self.backup_root = app_root / "backups"

    @property
    def executable(self) -> Path:
        return self.runtime_dir / "SCEWIN_64.exe"

    def ensure_ready(self) -> None:
        if os.name != "nt":
            raise ScewinExecutionError("Live SCEWIN mode is only available on Windows.")
        if not is_windows_admin():
            raise ScewinExecutionError(
                "Administrator access is required. Close the app and launch it with run.bat."
            )

        required = ["SCEWIN_64.exe", "amifldrv64.sys", "amigendrv64.sys"]
        missing = [name for name in required if not (self.asset_dir / name).is_file()]
        if missing:
            raise ScewinExecutionError(
                "The bundled SCEWIN runtime is incomplete: " + ", ".join(missing)
            )

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        for name in required:
            source = self.asset_dir / name
            destination = self.runtime_dir / name
            if not destination.exists() or sha256_file(source) != sha256_file(destination):
                shutil.copy2(source, destination)

    def _run(self, log_name: str, *args: str, timeout: int = 180) -> ScewinRunResult:
        self.ensure_ready()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [str(self.executable), *args],
                cwd=str(self.runtime_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b"").decode("latin-1", errors="replace")
            log_path = self.runtime_dir / log_name
            log_path.write_text(output, encoding="utf-8", errors="replace")
            raise ScewinExecutionError(
                f"SCEWIN did not finish within {timeout} seconds. See {log_path}."
            ) from exc
        except OSError as exc:
            raise ScewinExecutionError(f"Could not start SCEWIN: {exc}") from exc

        output = completed.stdout.decode("latin-1", errors="replace")
        log_path = self.runtime_dir / log_name
        log_path.write_text(output, encoding="utf-8", errors="replace")
        return ScewinRunResult(completed.returncode, output, log_path)

    @staticmethod
    def _is_valid_export(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size < 100:
            return False
        try:
            head = path.read_bytes()[:2_000_000].decode("latin-1", errors="ignore")
        except OSError:
            return False
        return "HIICrc32=" in head and "Setup Question\t=" in head

    def export_current(self, tag: str = "current") -> LiveExport:
        self.ensure_ready()
        safe_tag = "".join(character for character in tag if character.isalnum() or character in "-_")
        nvram_name = f"nvram_{safe_tag}.txt"
        dupes_name = f"Dupes_{safe_tag}.txt"
        profile_name = f"profile_{safe_tag}.json"
        nvram_path = self.runtime_dir / nvram_name
        dupes_path = self.runtime_dir / dupes_name
        profile_path = self.runtime_dir / profile_name
        for path in (nvram_path, dupes_path, profile_path):
            path.unlink(missing_ok=True)

        result = self._run(
            f"{safe_tag}_export.log", "/O", "/S", nvram_name, "/SD", dupes_name
        )
        if not self._is_valid_export(nvram_path):
            detail = result.output.strip() or f"SCEWIN exited with code {result.returncode}."
            raise ScewinExecutionError(
                "SCEWIN did not create a valid NVRAM export.\n\n" + detail
            )

        write_live_profile(
            nvram_path,
            dupes_path if dupes_path.exists() else None,
            profile_path,
            label="NVRAM",
        )
        profile = ParsedProfile.load(profile_path)
        document = ScewinDocument(nvram_path)
        document.verify_profile(profile)
        return LiveExport(
            profile=profile,
            document=document,
            profile_path=profile_path,
            nvram_path=nvram_path,
            dupes_path=dupes_path if dupes_path.exists() else None,
            run_result=result,
        )

    def prepare_apply(
        self,
        old_profile: ParsedProfile,
        changes: Iterable[PendingChange],
    ) -> ApplyPlan:
        queued = list(changes)
        if not queued:
            raise ValidationError("Queue at least one setting change first.")
        live = self.export_current("preapply")
        remapped = remap_changes(old_profile, live.profile, live.document, queued)
        return ApplyPlan(live_export=live, changes=remapped)

    def execute_apply(self, plan: ApplyPlan) -> ApplyResult:
        self.ensure_ready()
        if not plan.changes:
            raise ValidationError("The apply plan contains no changes.")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = self.backup_root / stamp
        backup_dir.mkdir(parents=True, exist_ok=False)

        before_path = backup_dir / "nvram_before.txt"
        shutil.copy2(plan.live_export.nvram_path, before_path)
        if plan.live_export.dupes_path and plan.live_export.dupes_path.exists():
            shutil.copy2(plan.live_export.dupes_path, backup_dir / "Dupes_before.txt")
        shutil.copy2(plan.live_export.run_result.log_path, backup_dir / "preapply_export.log")

        apply_name = "nvram_apply.txt"
        apply_path = self.runtime_dir / apply_name
        apply_path.unlink(missing_ok=True)
        plan.live_export.document.write_modified_copy(apply_path, plan.changes)
        shutil.copy2(apply_path, backup_dir / apply_name)

        transaction_path = backup_dir / "transaction.json"
        transaction = {
            "schema_version": 1,
            "dry_run_only": False,
            "created": datetime.now().isoformat(timespec="seconds"),
            "hii_crc32": plan.live_export.document.hii_crc32,
            "source_nvram": str(plan.live_export.nvram_path),
            "source_sha256": plan.live_export.document.source_sha256,
            "backup_folder": str(backup_dir),
            "changes": [change.to_dict() for change in plan.changes],
        }
        transaction_path.write_text(json.dumps(transaction, indent=2), encoding="utf-8")

        import_result = self._run("last_import.log", "/I", "/S", apply_name)
        shutil.copy2(import_result.log_path, backup_dir / "import.log")
        transaction["import_returncode"] = import_result.returncode
        transaction["import_output"] = import_result.output
        transaction_path.write_text(json.dumps(transaction, indent=2), encoding="utf-8")

        try:
            verified = self.export_current("verify")
        except BiosManagerError as exc:
            transaction["verification_error"] = str(exc)
            transaction_path.write_text(json.dumps(transaction, indent=2), encoding="utf-8")
            raise ScewinExecutionError(
                "SCEWIN import ran, but a verification export could not be created.\n\n"
                f"Backup: {backup_dir}\n\n{exc}"
            ) from exc

        shutil.copy2(verified.nvram_path, backup_dir / "nvram_after_verify.txt")
        shutil.copy2(verified.run_result.log_path, backup_dir / "verify_export.log")
        mismatches = verify_changes(verified.profile, plan.changes)
        transaction["verification_mismatches"] = mismatches
        transaction_path.write_text(json.dumps(transaction, indent=2), encoding="utf-8")

        if mismatches:
            detail = "\n".join(mismatches[:20])
            if len(mismatches) > 20:
                detail += f"\n…and {len(mismatches) - 20} more"
            raise ScewinExecutionError(
                "SCEWIN completed, but one or more values did not verify.\n\n"
                f"{detail}\n\nBackup: {backup_dir}"
            )

        return ApplyResult(
            backup_dir=backup_dir,
            verified_export=verified,
            import_result=import_result,
            verified_count=len(plan.changes),
        )
