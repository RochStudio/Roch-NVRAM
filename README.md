# Roch NVRAM 1.0.1

Roch NVRAM is a Windows editor for AMI SCEWIN NVRAM exports. It reads the live NVRAM,
lets you queue setting changes, and writes them only after a backup, a fresh re-export
that confirms nothing moved, and a value-by-value verification afterwards.

It is the SCEWIN round trip with a review step in the middle. SCEWIN's own `Export.bat`
and `Import.bat` dump the NVRAM to a text file and write that file back; here the same
two steps are buttons, and between them you see exactly which settings will change.

Bundled AMISCE: **5.05.01.0002**. See [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

> **This program writes firmware settings.** Incorrect voltage, clock, memory, security,
> or boot values can stop a machine from starting. Keep Clear CMOS or BIOS Flashback
> available before you use it.

## Run

**From a packaged build** (nothing to install — Python and Qt are inside the executable):

1. Extract the folder: `RochNVRAM.exe` with `scewin/` beside it.
2. Run `RochNVRAM.exe`, and approve the Windows administrator prompt.

The executable requests those rights through its own manifest, so that layout has no
`run.bat`. `scewin/` sits next to the executable rather than inside it, which is what
lets the AMI binaries be replaced without a rebuild.

**From source** (needs Python 3.10 or newer):

1. `install.bat` once, to create `.venv` and install PySide6.
2. `run.bat`, and approve the administrator prompt.

The window opens with nothing loaded and does not touch the firmware. Press **Export**
to read the live settings. The administrator prompt appears at launch rather than on
demand because SCEWIN needs those rights the moment you press Export or Import, and a
running process cannot elevate itself.

## Export and Import

The two halves of the round trip, side by side at the head of the NVRAM tab:

- **Export** runs SCEWIN against the live NVRAM and loads the result into the table.
  Nothing is read from the firmware until you press it.
- **Import** writes the queued changes. Unlike `Import.bat`, which writes a file as it
  stands, this re-exports first and refuses to continue when:
  - the HII CRC changed,
  - a queued setting is missing,
  - its current value changed since you queued it,
  - or it became malformed or ambiguous.

  It then takes a timestamped backup, runs `SCEWIN_64.exe /I /S nvram_apply.txt`, exports
  again, and verifies every requested value. The file it writes is byte-for-byte the live
  export outside the queued records — line endings included, so the doubled CRs AMISCE
  puts in its own header survive untouched.

Everything else needs an NVRAM in the table, so the remaining buttons stay disabled until
you export one. Compare and Log work on their own files and are always available.

Backups and transaction files:

```text
%LOCALAPPDATA%\NVRAM\backups
```

## NVRAM tab

The table preserves the exact record order of `nvram.txt` and shows Setting, Help String,
Token, Offset, Width, BIOS Default, and Options/Value. Search across names, help text,
tokens, offsets, and export IDs; filter by type or by editable/warning state.

**Queue selected change** (or a double-click) edits an entry. Settings the parser could
not read unambiguously are greyed out and refused rather than guessed at.

## Quick Settings tab

The overclocking controls of a platform on one page, in three sections: **CPU**, **RAM**,
and **GPU / PCIe**, so the settings that matter for tuning are together instead of spread
through 5,000 rows. Each row shows the setting's current value and an editor that starts
on it; set a new value and press **Queue**. It is the same queue the NVRAM table feeds,
so Import still runs the preflight, backup, and verification around whatever you queue
here. No values are suggested: the tab decides what to show, not what to set.

A preset is the list of settings. `assets/quick_settings/msi_z790_ddr5.json` was built
from a stock export and a tuned export of an MSI Z790 MPOWER: every setting whose value
differs between the two is a row, plus a standing list in `tools/make_quick_settings.py`
of controls that belong on the page regardless (Hyper-Threading, XMP, the common timings).
Rows match the live NVRAM by **token + offset + width**;
a row the loaded firmware does not have says so and stays disabled. One row can write
several identities, which is how the per-channel copies of a DDR5 timing become a single
control.

**Presets are per vendor.** MSI, ASUS, ASRock, and Gigabyte lay their NVRAM out
differently -- different identities, names, order, and which settings exist at all -- so
an MSI preset finds nothing on an ASUS board. That is deliberate: matching by name across
vendors would be guessing, and this program writes firmware. When a loaded export is from
another family the tab says so at the top, with the count of controls found, instead of
showing a page of disabled rows. Each vendor gets its own preset, built the same way.

To build one, export once at stock and once tuned on that board, then run

```bash
py tools/make_quick_settings.py stock.txt tuned.txt --vendor ASUS --board "ROG Maximus Z790 Apex" --name "ASUS Z790 (LGA 1700 DDR5)" --platform asus-z790-ddr5 --out assets/quick_settings/asus_z790_ddr5.json
```

It sorts the differences into the three sections by name, drops fan curves and other
changes that are not overclocking controls, and lists anything it could not place.

## Load NVRAM

Restores settings from a previously saved `nvram.txt`, for example after a Clear CMOS.
The saved file is never written wholesale: its settings are matched to the live export by
**token + offset + width** and every difference is queued as an ordinary pending change,
so the preflight, backup, and verification above still apply. Review the queue, then
press Import.

The dialog defaults to the archive of every NVRAM the tool has captured, and reports how
many settings will be queued, already match, or were skipped. It warns when the saved
file's HII CRC differs from the live one.

## Pending Changes tab

Setting, Help String, Before, and After for each queued change. Remove entries, clear the
queue, export a modified `nvram.txt`, or export a transaction JSON without writing
anything.

## Compare tab

Browse for **NVRAM 1** and **NVRAM 2**; changed settings are shown by default. Matching
uses token + offset + width and preserves NVRAM 1's record order.

**Only value changes count.** A setting renamed between BIOS builds whose value is
unchanged is reported as Same, with the differing name shown for context. Type changes
and settings present in only one file count as changed. If the two files report different
HII CRC values the app warns, because their offsets may describe different BIOS layouts.
**Export comparison CSV** saves the changed rows.

## Log tab

A catalog of every NVRAM the tool has opened — not a log of changes. Each row records
Date & Time, Source, SCEWIN version, HII CRC32, setting count, SHA-256, and the archived
file. A verbatim copy of every capture is kept, content-addressed by SHA-256 so identical
re-opens are not duplicated. Double-click a row to open that exact export.

```text
%LOCALAPPDATA%\NVRAM\logs\nvram_catalog.jsonl   catalog
%LOCALAPPDATA%\NVRAM\logs\nvram\                archived nvram.txt files
%LOCALAPPDATA%\NVRAM\logs\nvram.log             activity audit trail
```

## Offline mode

`run_offline.bat` is a dry run. It never executes SCEWIN, never writes firmware, needs no
administrator rights, and hides Export and Import. Compare and Log still work.

It needs a sample export in `data/` — an `nvram.txt` and its parsed `.json`. Those are
board-specific and are not tracked, so a fresh clone has no `data/`: point the app at your
own export with `--profile` and `--nvram`, or generate a pair with

```bash
python -m bios_manager.scewin_parser <nvram.txt> --output data/profile.json
```

Live mode and the tests are unaffected; the tests use their own committed fixture.

## Safety notes

- **If an import reports that values did not verify, check that "Password protection of
  Runtime Variables" is Disabled in BIOS setup.** That setting locks the UEFI runtime
  variable interface, so SCEWIN exits cleanly while its writes are silently dropped.
- A backup is taken before every import, but there is no automatic rollback. To revert,
  import `nvram_before.txt` from the backup folder.
- A successful import may still need a reboot.
- SCEWIN is an AMI utility bundled here for convenience. This project does not establish
  its authenticity or its own right to redistribute it — see
  [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

## Tests

```bash
run_tests.bat
```

All 79 tests run on a fresh clone. They cover parsing, record order, option and value
edits, byte-for-byte rewriting, stale-value rejection, profile matching, verification,
comparison results, CSV export, the catalog and its archiving, the Quick Settings presets
and the tab that queues from them, the window's own button states, and that the version
is written in exactly one place.

The export they read is committed at `tests/fixtures/nvram.txt`: a small synthetic SCEWIN
file, not a board dump, keeping the record shapes real ones have. Its parsed profile is
generated during the run rather than committed beside it, so the two cannot drift apart.
Nothing in the suite executes SCEWIN or touches firmware.

24 of them need PySide6 and skip cleanly without it, so `run_tests.bat` uses `.venv` when
it is there and falls back to the system interpreter with a note when it is not.

## Building

```bash
py -m pip install pyinstaller
py -m PyInstaller --clean -y RochNVRAM.spec
```

That produces `dist\RochNVRAM.exe`, a single file with Python, Qt and the icon inside
it. Copy `scewin/` beside the executable to run it.

Releases are built on Python 3.14, the newest PySide6 accepts: it ships a single abi3
wheel and requires `>=3.10,<3.15`, so 3.10 is the floor and 3.15 will not install until
PySide6 says otherwise.

## Credits

- **[SCEHUB](https://github.com/ab3lkaizen/SCEHUB)** by ab3lkaizen — packages AMISCE with
  the `Export.bat` / `Import.bat` pair this program is built around. The Export and Import
  buttons are those two steps with a review in between.
- **AMISCE** is American Megatrends' utility. Every read and write goes through it; this
  project only decides what to ask it for.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).

Third-party components, including the bundled AMI binaries that are **not** covered by
that licence, are listed in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
