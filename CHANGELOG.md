# Changelog

## v1.0.0

First public release. Licensed GPL-3.0-or-later; third-party components, including the
bundled AMI binaries that licence does not cover, are listed in `THIRD_PARTY_NOTICES.txt`.


### Reading and writing the NVRAM

- The app no longer reads the firmware when it opens. It starts with nothing loaded, and the
  NVRAM tab leads with the SCEWIN round trip as two adjacent buttons: **Export** reads the live
  NVRAM on demand, and **Import** writes the queue you have reviewed. These are the halves SCEWIN itself
  ships as `Export.bat` and `Import.bat`. Import is the former Apply button, with the same
  preflight, backup, and verification around the write.
- **Load NVRAM...** queues the differences from a saved export, for example after a Clear CMOS,
  and Import writes them.
- Editing, loading, exporting, and importing stay disabled until an NVRAM is loaded.
- The generated import file is now byte-for-byte identical to the export it came from except
  inside the edited records. The document normalised line endings on the way in and re-expanded
  every newline to CRLF on the way out, so a real AMISCE export — which writes a doubled CR on
  two of its header lines — came back two bytes larger with two extra blank lines, and an
  LF-only file was silently rewritten to CRLF.

### Fixes

- Fixed an import file that could carry **two selected options** for one setting. The
  writer stopped scanning an Options block at the first unindented row and left that
  row's `*` in place; reader and writer now share one rule for what an option row is.
- Fixed a queued option change being **silently discarded** when two option codes share
  the same label. The editor compared displayed text; it now compares raw values.
- Values that a SCEWIN export's Latin-1 encoding cannot store are refused when queued,
  instead of raising an unhandled error part-way through the write.
- Missing, unreadable, or malformed profiles and exports now report a normal error
  dialog. They previously escaped every handler and, because the launchers use
  `pythonw.exe`, closed the app with no window and no message.
- Offline mode now says which sample files are missing instead of exiting silently when
  `data/` is absent, which is the normal state of a fresh clone.
- Compare no longer reports a spurious change for records that carry no token, offset,
  or width; two identical files now compare as unchanged.

### Project

- Added `RochNVRAM.spec` and `file_version_info.txt`, so the packaged build is reproducible
  from the repository: a single `RochNVRAM.exe` carrying Python, Qt and the icon, requesting
  administrator rights through its manifest. `scewin/` stays outside the executable so the
  AMI binaries can be replaced without rebuilding. CI builds it on every run.
- CI runs on Python 3.14, the newest PySide6 accepts (`>=3.10,<3.15`), and uses the
  Node 24 actions.
- Licensed GPL-3.0-or-later, matching Roch Viewer. Added `LICENSE`, a source header to every
  file, `THIRD_PARTY_NOTICES.txt`, and a GitHub Actions workflow that runs the suite on
  Windows.
- Credited SCEHUB, whose `Export.bat` / `Import.bat` pair this program is built around, and
  Roch Viewer, the companion project it shares an icon and its conventions with.
- Collapsed duplicated code: one `sha256_file` and one `raw_value` instead of two of each,
  one routine for showing a freshly opened export instead of the same twelve lines in two
  places, and a `_busy` context manager in place of a try/except/finally that restored the
  window by inspecting the cursor stack. That last one also fixed the Import button
  re-enabling itself after a failed export, with no NVRAM loaded.

- The whole test suite now runs on a fresh clone. `test_backend.py` and `test_core.py` loaded a
  board-specific dump from the untracked `data/` folder, so 11 of the tests could not run for
  anyone who cloned the repository. They now read a small synthetic export committed at
  `tests/fixtures/nvram.txt`, and derive its parsed profile during the run instead of keeping a
  second copy in git. 46 tests, all passing.
- The application icon is now the Roch Studio logo shared with Roch Viewer. Removed
  `tools/make_icon.py`, which drew the previous placeholder and would have silently reverted the
  logo if anyone ran it.
- Added `.gitattributes`. Line-ending normalisation is now a property of the repository rather
  than of each clone's `core.autocrlf`, the bundled SCEWIN binaries are marked explicitly rather
  than left to detection, and the SCEWIN test fixtures are stored byte for byte.

## v0.2.0

- Renamed the application to **Roch NVRAM** and added an application icon.
- Ships as a standalone build; Python and Qt are bundled and no install step is required.
- Replaced the Log tab's change log with a **catalog of every NVRAM the tool opens**, newest
  first, recording date/time, source, SCEWIN version, HII CRC32, setting count, and SHA-256.
- The actual `nvram.txt` is archived verbatim for every capture, content-addressed by SHA-256,
  and can be opened from the Log tab.
- Added **Load NVRAM**, which restores settings from a saved export (for example after a Clear
  CMOS) by queueing the differences for review instead of importing the file wholesale.
- Compare now counts **value changes only**; a setting renamed between BIOS builds with an
  unchanged value is no longer reported as changed.
- Pending Changes columns are now Setting, Help String, Before, and After.
- Removed the SCEWIN version from the header line; it is shown per capture in the Log tab.
- Renamed the edit dialog's "Firmware help" label to "Help String".
- Removed dead code: unused imports, an unreachable log-event filter, a no-op `closeEvent`,
  and three write-only fields. Pending Changes no longer rebuilds a list per table cell.
- Tests expanded to 20.

## v0.1.6

- Removed the live-mode information banner from the top of the normal NVRAM window.
- Kept the dry-run warning banner in offline mode.
- Renamed the generated live profile label from **Live SCEWIN NVRAM** to **NVRAM**.

## v0.1.5

- Renamed the application window from **SCEWIN NVRAM Manager** to **NVRAM**.
- Renamed Compare inputs and value columns to **NVRAM 1** and **NVRAM 2**.
- Updated comparison status and CSV labels to use NVRAM 1/NVRAM 2.
- Limited the persistent Log tab to NVRAM-related activity only.
- Renamed the persistent log file to `nvram.log` under `%LOCALAPPDATA%\NVRAM\logs`.

## v0.1.4

- Added a **Compare** tab for NVRAM 1 versus NVRAM 2 `nvram.txt` files.
- Added changed-only, all-entry, and same-only comparison filters.
- Added search across compared setting names, values, tokens, offsets, and statuses.
- Preserved NVRAM 1's original record order in comparison results.
- Added HII CRC mismatch warnings for files from different BIOS layouts.
- Added changed-setting CSV export.
- Added a persistent **Log** tab with local date and time on every entry.
- Logs every NVRAM open, queued/removed change, Apply action/result, export, and comparison.
- Manual matching NVRAM/profile opens now load immediately instead of requiring a restart.
- Added comparison and logging tests; 15 tests pass.

## v0.1.3

- Returned to the original v0.1.2 PySide6 interface.
- Added **Apply** buttons to the NVRAM and Pending Changes tabs.
- Added automatic live NVRAM export on startup.
- Added administrator elevation through `run.bat`.
- Added fresh pre-apply export and stale-value detection.
- Added timestamped backups and transaction logs before import.
- Added post-import export and exact value verification.
- Added `run_offline.bat` for the original dry-run mode.
- Bundled the user-supplied SCEWIN executable and two drivers.
- Added backend tests; 11 tests pass.

## v0.1.2

- Renamed the main settings view to NVRAM.
- Preserved exact source order.
- Changed columns to Setting, Help String, Token, Offset, Width, BIOS Default, and Options/Value.
