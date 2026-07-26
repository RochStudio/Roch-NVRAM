from __future__ import annotations

import argparse
import sys
from pathlib import Path


def base_dir() -> Path:
    """Directory holding data/ and scewin/.

    When frozen by PyInstaller the sources live inside the bundle, so anchor on
    the executable instead of __file__ to keep the runtime assets alongside it.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="NVRAM")
    base = base_dir()
    parser.add_argument(
        "--profile",
        type=Path,
        default=base / "data" / "z890_tachyon_nvram_parsed.json",
    )
    parser.add_argument(
        "--nvram",
        type=Path,
        default=base / "data" / "z890_tachyon_nvram.txt",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Open the bundled export without running or importing through SCEWIN.",
    )
    args = parser.parse_args()

    try:
        from bios_manager.gui import run
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            print(
                "PySide6 is not installed. Run install.bat or execute:\n"
                "  python -m pip install -r requirements.txt",
                file=sys.stderr,
            )
            return 2
        raise

    return run(args.profile, args.nvram, project_root=base, live=not args.offline)


if __name__ == "__main__":
    raise SystemExit(main())
