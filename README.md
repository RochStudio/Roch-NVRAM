# Roch NVRAM 1.0.0

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

**From a packaged build** (nothing to install — Python and Qt come with it):

1. Extract the whole `Roch NVRAM` folder.
2. `run.bat`, and approve the Windows administrator prompt.

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

All 48 tests run on a fresh clone. They cover parsing, record order, option and value
edits, byte-for-byte rewriting, stale-value rejection, profile matching, verification,
comparison results, CSV export, the catalog and its archiving, and the window's own
button states.

The export they read is committed at `tests/fixtures/nvram.txt`: a small synthetic SCEWIN
file, not a board dump, keeping the record shapes real ones have. Its parsed profile is
generated during the run rather than committed beside it, so the two cannot drift apart.
Nothing in the suite executes SCEWIN or touches firmware.

13 of the tests need PySide6 and skip cleanly without it. `run_tests.bat` uses the system
interpreter, so it reports them as skipped; run the suite from the venv to include them:

```bash
.venv\Scripts\python.exe -m unittest discover -s tests
```

## Credits

- **[SCEHUB](https://github.com/ab3lkaizen/SCEHUB)** by ab3lkaizen — packages AMISCE with
  the `Export.bat` / `Import.bat` pair this program is built around. The Export and Import
  buttons are those two steps with a review in between.
- **[Roch Viewer](https://github.com/RochStudio/Roch-Viewer)** — the companion project.
  It reads memory-controller and timing state and changes nothing, where this one writes
  firmware settings. The icon is shared, and the repository conventions here follow the
  ones it established.
- **AMISCE** is American Megatrends' utility. Every read and write goes through it; this
  project only decides what to ask it for.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).

Third-party components, including the bundled AMI binaries that are **not** covered by
that licence, are listed in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
