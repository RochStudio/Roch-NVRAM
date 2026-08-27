# Roch NVRAM v0.2.0

A PySide6 desktop editor for AMI SCEWIN NVRAM exports. It reads the live NVRAM through the
bundled SCEWIN runtime, lets you queue setting changes, and imports them only after a
backup, a preflight re-export, and a value-by-value verification.

Bundled SCEWIN: **AMISCE 5.05.01.0002**.

## Run

**Standalone package** (nothing to install — Python and Qt are bundled):

1. Extract the whole `Roch NVRAM` folder from the zip.
2. Run `run.bat` and approve the Windows administrator prompt.

**From source** (needs Python 3):

1. Run `install.bat` once to create `.venv` and install PySide6.
2. Run `run.bat` and approve the administrator prompt.

The app opens with nothing loaded and does not touch the firmware. Press **Export**
to read the live settings. The administrator prompt still appears at launch because SCEWIN
needs it the moment you press Export or Import. Live mode never uses a sample file.

## Export and Import

These are the two halves of the SCEWIN round trip, the same split as SCEWIN's own
`Export.bat` and `Import.bat`, and both live in the NVRAM tab:

- **Export** reads the live NVRAM through the bundled SCEWIN runtime and loads it into the
  table. Nothing is read from the firmware until you press it.
- **Import** writes the queued changes back. Unlike `Import.bat`, which runs SCEWIN on a file
  as it stands, you review the exact list first and the preflight, backup, and verification
  below run around the write.

Editing, loading, and importing all need an NVRAM in the table, so those buttons stay
disabled until you export one. Compare and Log work on their own files and are always
available.

## Offline mode

Run `run_offline.bat` for the dry run. It never executes SCEWIN, never writes firmware, needs
no administrator rights, and hides the Export and Import buttons. Compare and Log remain available.

Offline mode needs a sample export in `data/` (an `nvram.txt` plus its parsed `.json`). These
are board-specific and are not tracked in git, so a fresh clone has no `data/` folder — point
the app at your own export with `--profile` and `--nvram`, or generate a pair with
`python -m bios_manager.scewin_parser <nvram.txt> --output data/profile.json`. Live mode and
the test suite are unaffected; the tests use their own committed fixture.

## NVRAM tab

The table preserves the exact record order of `nvram.txt` and shows Setting, Help String,
Token, Offset, Width, BIOS Default, and Options/Value. Search across names, help text, tokens,
offsets, and export IDs; filter by type or by editable/warning state.

- **Export** reads the live NVRAM (live mode only).
- **Import** writes the queue (live mode only). It sits beside Export: the two are the
  round trip, and the editing controls follow them.
- **Queue selected change** (or double-click a row) edits an entry.
- **Load NVRAM...** queues the differences from a saved export without writing — see below.

## Load NVRAM

Queues the differences from a previously saved `nvram.txt`, for example after a Clear CMOS,
without writing anything — review them, then press **Import**. The saved file is never
imported wholesale. Its settings are matched to the live export by
**token + offset + width**, and every difference is queued as a normal pending change, so the
usual preflight, backup, and verification still apply.

The dialog defaults to the archive of every NVRAM the tool has captured
(`%LOCALAPPDATA%\NVRAM\logs\nvram`) and reports how many settings will be queued, already
match, or were skipped. It warns when the saved file's HII CRC differs from the live one.
Nothing is written until you review the queue and press Import.

## Pending Changes tab

Lists Setting, Help String, Before, and After for each queued change. You can remove entries,
clear the queue, export a modified `nvram.txt`, or export a transaction JSON without applying.

## Compare tab

1. Browse for **NVRAM 1** and **NVRAM 2** exports.
2. Changed settings are shown by default; switch to all entries or same-only.

Matching uses token + offset + width and preserves NVRAM 1's record order. **Only value
changes count.** A setting whose label differs between BIOS builds but whose value is
unchanged is reported as Same — the differing name is still shown for context. Type changes
and settings present in only one file also count as changed.

If the two files have different HII CRC values the app warns, because their offsets may
describe different BIOS layouts. Use **Export comparison CSV** to save the changed rows.

## Log tab

A catalog of every NVRAM the tool has opened — not a log of changes. A new dated entry is
added each time the tool starts and whenever another export is loaded (manual open, live
refresh after apply, or a Compare selection). Newest entries appear first.

Each row records Date & Time, Source, SCEWIN version, HII CRC32, setting count, SHA-256, and
the archived NVRAM file. A verbatim copy of the actual `nvram.txt` is stored for every
capture, content-addressed by SHA-256 so identical re-opens are not duplicated. Double-click a
row (or use **Open NVRAM file**) to open that exact export.

```text
%LOCALAPPDATA%\NVRAM\logs\nvram_catalog.jsonl   catalog
%LOCALAPPDATA%\NVRAM\logs\nvram\                archived nvram.txt files
%LOCALAPPDATA%\NVRAM\logs\nvram.log             activity audit trail
```

## Importing a change

1. Queue one or more changes.
2. Review them in **Pending Changes**.
3. Press **Import** and confirm the before/after summary.

Before importing, the app performs another live export and refuses to continue when:

- The HII CRC changed.
- A queued setting is missing.
- The current value changed since it was queued.
- The setting became malformed or ambiguous.

The app then creates a timestamped backup, generates an import file where only the queued
records are modified, runs `SCEWIN_64.exe /I /S nvram_apply.txt`, exports NVRAM again, and
verifies every requested value. That import file is byte-for-byte the live export outside the
queued records, line endings included, so the doubled CRs AMISCE writes into its own header
survive untouched. Backups and transaction files are stored under:

```text
%LOCALAPPDATA%\NVRAM\backups
```

## Safety notes

- **If an apply reports that values did not verify, check that "Password protection of Runtime
  Variables" is Disabled in BIOS setup.** That setting locks the UEFI runtime variable
  interface, so SCEWIN exits cleanly while its writes are silently dropped.
- A backup is created before each import; there is no automatic rollback. To revert, use
  `nvram_before.txt` from the backup folder.
- A successful import may still require a reboot.
- Incorrect voltage, clock, memory, security, or boot settings can prevent startup. Keep Clear
  CMOS or BIOS Flashback available.
- SCEWIN is shipped inside MSI Center and is bundled here for convenience; this project does
  not establish its authenticity or redistribution rights.

## Tests

Run `run_tests.bat`. The suite covers parsing, source order, option and value edits,
stale-value rejection, profile matching, verification, comparison results (including
value-only matching), CSV export, the NVRAM catalog and its archiving, and protection against
overwriting the source export.

All 42 tests run on a fresh clone. The export they read is committed at
`tests/fixtures/nvram.txt`: a small synthetic SCEWIN file, not a board dump, that keeps the
record shapes the real ones have. Its parsed profile is generated during the test run rather
than committed beside it, so the two cannot drift apart.

The 7 in `test_import_export.py` need PySide6 and skip cleanly without it, so run the suite
from the venv (`.venv\Scripts\python.exe -m unittest discover -s tests`) to include them.
`run_tests.bat` uses the system interpreter and reports them as skipped.
