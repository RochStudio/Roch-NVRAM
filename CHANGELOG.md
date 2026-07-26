# Changelog

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
