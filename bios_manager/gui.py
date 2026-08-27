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

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QUrl,
)
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .activity_log import ActivityLogger
from .nvram_catalog import CatalogEntry, NvramCatalog
from .compare_tools import (
    CompareResult,
    CompareRow,
    NvramSnapshot,
    compare_snapshots,
    export_compare_csv,
    has_identity,
    identity_without_crc,
    load_snapshot,
)
from .core import (
    BiosManagerError,
    ParsedProfile,
    PendingChange,
    ScewinDocument,
    raw_value,
    write_transaction,
)
from .scewin_backend import ApplyResult, LiveExport, ScewinBackend


APP_NAME = "Roch NVRAM"


def asset_path(name: str) -> Path:
    """Locate a bundled asset, both from source and inside a PyInstaller build."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parents[1]
    return base / "assets" / name


def app_icon() -> QIcon | None:
    path = asset_path("roch_nvram.ico")
    return QIcon(str(path)) if path.is_file() else None


class SavedNvramDiff(NamedTuple):
    """What a saved nvram.txt would change in the live NVRAM."""

    queued: list[PendingChange]
    blocked: list[str]
    missing: int
    already: int


COLUMNS = [
    ("question", "Setting"),
    ("help", "Help String"),
    ("token_hex", "Token"),
    ("offset_hex", "Offset"),
    ("width_hex", "Width"),
    ("bios_default", "BIOS Default"),
    ("options_value", "Options/Value"),
]


class SettingsModel(QAbstractTableModel):
    def __init__(self, settings: list[dict[str, Any]], changes: dict[str, PendingChange]):
        super().__init__()
        self.settings = settings
        self.changes = changes

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.settings)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section][1]
        return None

    def setting_at(self, row: int) -> dict[str, Any]:
        return self.settings[row]

    @staticmethod
    def _bios_default(setting: dict[str, Any]) -> str:
        raw = setting.get("bios_default_raw")
        return "Unknown" if raw in (None, "") else str(raw)

    @staticmethod
    def _options_or_value(setting: dict[str, Any]) -> str:
        if setting.get("kind") == "options":
            rendered: list[str] = []
            for option in setting.get("options") or []:
                marker = "*" if option.get("selected") else ""
                code = str(option.get("code_hex") or "")
                label = str(option.get("label") or "")
                rendered.append(f"{marker}[{code}]{label}")
            return " | ".join(rendered) if rendered else "Unknown"

        value = setting.get("current_value")
        return "Unknown" if value is None else f"<{value}>"

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        setting = self.settings[index.row()]
        key = COLUMNS[index.column()][0]
        change = self.changes.get(str(setting.get("export_id")))

        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            if key == "bios_default":
                return self._bios_default(setting)
            if key == "options_value":
                return self._options_or_value(setting)
            value = setting.get(key)
            return "" if value is None else str(value)

        if role == Qt.ForegroundRole:
            editable, _ = ScewinDocument.is_editable(setting)
            if not editable:
                return QBrush(QColor("#888888"))
            if change:
                return QBrush(QColor("#d97706"))

        if role == Qt.FontRole and change:
            font = QFont()
            font.setBold(True)
            return font
        return None

    def refresh(self):
        if self.settings:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.settings) - 1, len(COLUMNS) - 1)
            )


class SettingsFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.search_text = ""
        self.kind = "All"
        self.warning_mode = "All"
        self.setDynamicSortFilter(True)

    def set_search(self, text: str):
        self.search_text = text.casefold().strip()
        self.invalidateFilter()

    def set_kind(self, kind: str):
        self.kind = kind
        self.invalidateFilter()

    def set_warning_mode(self, mode: str):
        self.warning_mode = mode
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model: SettingsModel = self.sourceModel()
        setting = model.setting_at(source_row)
        if self.kind == "Dropdown" and setting.get("kind") != "options":
            return False
        if self.kind == "Value" and setting.get("kind") != "value":
            return False
        warnings = setting.get("warnings") or []
        if self.warning_mode == "Warnings only" and not warnings:
            return False
        if self.warning_mode == "Editable only" and not ScewinDocument.is_editable(setting)[0]:
            return False
        if not self.search_text:
            return True
        haystack = " ".join(
            str(setting.get(field) or "")
            for field in ("question", "help", "token_hex", "offset_hex", "export_id")
        ).casefold()
        return self.search_text in haystack


class ChangeModel(QAbstractTableModel):
    headers = ["Setting", "Help String", "Before", "After"]

    def __init__(self, changes: dict[str, PendingChange]):
        super().__init__()
        self.changes = changes
        # Snapshot of the queue, rebuilt by refresh(). rowCount and data read the
        # same list so they cannot disagree, and no list is built per cell.
        self._rows: list[PendingChange] = list(changes.values())

    def items(self) -> list[PendingChange]:
        return self._rows

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or not index.isValid():
            return None
        change = self._rows[index.row()]
        return [
            change.question,
            change.help,
            change.old_display,
            change.new_display,
        ][index.column()]

    def refresh(self):
        self.beginResetModel()
        self._rows = list(self.changes.values())
        self.endResetModel()


class CompareModel(QAbstractTableModel):
    headers = [
        "Setting",
        "Help String",
        "NVRAM 1 Value",
        "NVRAM 2 Value",
        "Token",
        "Offset",
        "Width",
        "Type",
        "Status",
    ]

    def __init__(self):
        super().__init__()
        self.rows: list[CompareRow] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return None

    def row_at(self, row: int) -> CompareRow:
        return self.rows[row]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        values = [
            row.setting,
            row.help,
            row.stock_value,
            row.overclocked_value,
            row.token_hex,
            row.offset_hex,
            row.width_hex,
            row.kind,
            row.status,
        ]
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return values[index.column()]
        if role == Qt.ForegroundRole and row.changed:
            return QBrush(QColor("#b45309"))
        if role == Qt.FontRole and row.changed:
            font = QFont()
            font.setBold(True)
            return font
        return None

    def set_rows(self, rows: list[CompareRow]):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()


class CompareFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.search_text = ""
        self.mode = "Changed only"
        self.setDynamicSortFilter(True)

    def set_search(self, text: str):
        self.search_text = text.casefold().strip()
        self.invalidateFilter()

    def set_mode(self, mode: str):
        self.mode = mode
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model: CompareModel = self.sourceModel()
        row = model.row_at(source_row)
        if self.mode == "Changed only" and not row.changed:
            return False
        if self.mode == "Same only" and row.changed:
            return False
        if not self.search_text:
            return True
        haystack = " ".join(
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
        ).casefold()
        return self.search_text in haystack


class CatalogModel(QAbstractTableModel):
    headers = [
        "Date & Time",
        "Source",
        "SCEWIN Ver",
        "HII CRC32",
        "Settings",
        "SHA-256",
        "NVRAM File",
    ]

    def __init__(self, entries: list[CatalogEntry]):
        super().__init__()
        # The catalog file is append-only (oldest first); the tab lists newest first.
        self.entries = list(reversed(entries))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return None

    def entry_at(self, row: int) -> CatalogEntry:
        return self.entries[row]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        entry = self.entries[index.row()]
        archived = Path(entry.archived_path).name if entry.archived_path else "—"
        display = [
            entry.timestamp,
            entry.source,
            entry.amisce_version or "?",
            entry.hii_crc32,
            f"{entry.settings:,}",
            entry.sha256,
            archived,
        ]
        if role == Qt.DisplayRole:
            return display[index.column()]
        if role == Qt.ToolTipRole:
            if index.column() == 6:
                return entry.archived_path or "No archived copy was stored."
            return display[index.column()]
        return None

    def set_entries(self, entries: list[CatalogEntry]):
        self.beginResetModel()
        self.entries = list(reversed(entries))
        self.endResetModel()

    def add_entry(self, entry: CatalogEntry):
        self.beginInsertRows(QModelIndex(), 0, 0)
        self.entries.insert(0, entry)
        self.endInsertRows()


class EditDialog(QDialog):
    def __init__(
        self,
        setting: dict[str, Any],
        document: ScewinDocument,
        profile: ParsedProfile,
        parent=None,
        live_mode: bool = False,
    ):
        super().__init__(parent)
        self.setting = setting
        self.document = document
        self.profile = profile
        self.change: PendingChange | None = None
        self.setWindowTitle("Queue NVRAM change")
        self.resize(560, 260)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Setting:", QLabel(str(setting.get("question") or "N/A")))
        form.addRow("Current:", QLabel(document.setting_display(setting)))
        form.addRow("Identity:", QLabel(str(setting.get("export_id") or "")))
        help_label = QLabel(str(setting.get("help") or "No firmware help text."))
        help_label.setWordWrap(True)
        form.addRow("Help String:", help_label)

        if setting.get("kind") == "options":
            self.editor = QComboBox()
            seen: set[str] = set()
            for option in setting.get("options") or []:
                code = str(option.get("code_hex") or "").upper()
                if code in seen:
                    continue
                seen.add(code)
                self.editor.addItem(f"{option.get('label')}  [{code}]", code)
            current = str(setting.get("current_code_hex") or "").upper()
            index = self.editor.findData(current)
            if index >= 0:
                self.editor.setCurrentIndex(index)
        else:
            self.editor = QLineEdit(str(setting.get("current_value") or ""))
            self.editor.setPlaceholderText("Decimal or 0x-prefixed hexadecimal")
        form.addRow("New value:", self.editor)
        layout.addLayout(form)

        warning = QLabel(
            "This queues a change. It is written only after you press Import and confirm."
            if live_mode
            else "Dry run: this queues a change but does not write firmware."
        )
        warning.setStyleSheet("font-weight: 600; color: #b45309;")
        layout.addWidget(warning)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept_change)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept_change(self):
        try:
            raw = self.editor.currentData() if isinstance(self.editor, QComboBox) else self.editor.text()
            self.change = self.document.build_change(
                self.profile, str(self.setting["export_id"]), str(raw)
            )
        except BiosManagerError as exc:
            QMessageBox.warning(self, "Cannot queue change", str(exc))
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(
        self,
        profile_path: Path | None,
        nvram_path: Path | None,
        backend: ScewinBackend | None = None,
    ):
        super().__init__()
        self.backend = backend
        self.live_mode = backend is not None
        self.loaded_buttons: list[QPushButton] = []
        app_root = backend.app_root if backend is not None else None
        self.logger = ActivityLogger(app_root)
        self.catalog = NvramCatalog(app_root)
        self.catalog_model = CatalogModel(self.catalog.entries())
        self.catalog_table: QTableView | None = None
        self.stock_snapshot: NvramSnapshot | None = None
        self.overclocked_snapshot: NvramSnapshot | None = None
        self.compare_result: CompareResult | None = None

        # Live mode opens with nothing loaded: reading the firmware is what the
        # Export button is for. Offline mode still opens its sample export.
        self.profile: ParsedProfile | None = None
        self.document: ScewinDocument | None = None
        if profile_path is not None and nvram_path is not None:
            self.profile = ParsedProfile.load(profile_path)
            self.document = ScewinDocument(nvram_path)
            self.document.verify_profile(self.profile)
        self.changes: dict[str, PendingChange] = {}
        self.settings_model = SettingsModel(
            self.profile.settings if self.profile is not None else [], self.changes
        )
        self.filter_model = SettingsFilter()
        self.filter_model.setSourceModel(self.settings_model)
        self.change_model = ChangeModel(self.changes)
        self.compare_model = CompareModel()
        self.compare_filter = CompareFilter()
        self.compare_filter.setSourceModel(self.compare_model)

        self.setWindowTitle(
            APP_NAME if self.live_mode
            else f"{APP_NAME} — Dry Run"
        )
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(1500, 850)
        self._build_menu()
        self._build_ui()
        self._update_status()
        self._update_loaded_actions()
        if self.document is not None:
            self._log_nvram_open("Startup")

    def _build_menu(self):
        menu = self.menuBar().addMenu("File")
        open_action = QAction("Open matching export and profile...", self)
        open_action.triggered.connect(self.open_pair)
        menu.addAction(open_action)
        export_action = QAction("Export modified nvram.txt...", self)
        export_action.triggered.connect(self.export_nvram)
        menu.addAction(export_action)
        transaction_action = QAction("Export transaction JSON...", self)
        transaction_action.triggered.connect(self.export_transaction)
        menu.addAction(transaction_action)

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)

        if not self.live_mode:
            banner = QLabel(
                "DRY RUN ONLY — This build never executes SCEWIN and never writes firmware variables."
            )
            banner.setStyleSheet(
                "padding: 10px; font-weight: 700; background: #fff7ed; color: #9a3412;"
            )
            root_layout.addWidget(banner)

        self.profile_line = QLabel()
        self.profile_line.setStyleSheet("font-weight: 600; padding: 4px;")
        self._update_profile_line()
        root_layout.addWidget(self.profile_line)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search setting, help text, token, offset, or export ID...")
        self.search.textChanged.connect(self.filter_model.set_search)
        controls.addWidget(self.search, 1)
        kind = QComboBox()
        kind.addItems(["All", "Dropdown", "Value"])
        kind.currentTextChanged.connect(self.filter_model.set_kind)
        controls.addWidget(kind)
        warning_filter = QComboBox()
        warning_filter.addItems(["All", "Editable only", "Warnings only"])
        warning_filter.currentTextChanged.connect(self.filter_model.set_warning_mode)
        controls.addWidget(warning_filter)
        root_layout.addLayout(controls)

        tabs = QTabWidget()
        tabs.addTab(self._settings_tab(), "NVRAM")
        tabs.addTab(self._changes_tab(), "Pending Changes")
        tabs.addTab(self._compare_tab(), "Compare")
        tabs.addTab(self._log_tab(), "Log")
        root_layout.addWidget(tabs, 1)

        self.setCentralWidget(root)

    def _settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.settings_table = QTableView()
        self.settings_table.setModel(self.filter_model)
        self.settings_table.setSortingEnabled(False)
        # Preserve the exact record sequence from nvram.txt. Sorting is deliberately
        # disabled so the first rows always match the source export order.
        self.settings_table.setSelectionBehavior(QTableView.SelectRows)
        self.settings_table.setAlternatingRowColors(True)
        self.settings_table.doubleClicked.connect(self.edit_selected)
        header = self.settings_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.settings_table)

        buttons = QHBoxLayout()
        if self.live_mode:
            # The SCEWIN round trip, as separate steps: Export reads the firmware
            # (/O) and Import writes the reviewed queue back (/I), the same two
            # halves SCEWIN ships as Export.bat and Import.bat.
            export_button = QPushButton("Export")
            export_button.setToolTip(
                "Read the live NVRAM through SCEWIN and load it into the table. "
                "Nothing is read from the firmware until you press this."
            )
            export_button.clicked.connect(self.export_live_nvram)
            buttons.addWidget(export_button)
            import_button = QPushButton("Import")
            import_button.setToolTip(
                "Write the queued changes through SCEWIN. A backup and a "
                "verification pass run around the import."
            )
            import_button.clicked.connect(self.apply_changes)
            self.loaded_buttons.append(import_button)
            buttons.addWidget(import_button)
        edit = QPushButton("Queue selected change")
        edit.clicked.connect(self.edit_selected)
        self.loaded_buttons.append(edit)
        buttons.addWidget(edit)
        load = QPushButton("Load NVRAM...")
        load.setToolTip(
            "Restore settings from a saved nvram.txt export (for example after a "
            "CMOS clear). Differences are queued for review before anything is written."
        )
        load.clicked.connect(self.load_nvram)
        self.loaded_buttons.append(load)
        buttons.addWidget(load)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return tab

    def _changes_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.change_table = QTableView()
        self.change_table.setModel(self.change_model)
        self.change_table.setSelectionBehavior(QTableView.SelectRows)
        change_header = self.change_table.horizontalHeader()
        change_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        change_header.setSectionResizeMode(1, QHeaderView.Stretch)
        change_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        change_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.change_table)
        buttons = QHBoxLayout()
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_change)
        clear = QPushButton("Clear all")
        clear.clicked.connect(self.clear_changes)
        export = QPushButton("Export modified nvram.txt")
        export.clicked.connect(self.export_nvram)
        self.loaded_buttons.append(export)
        transaction = QPushButton("Export transaction JSON")
        transaction.clicked.connect(self.export_transaction)
        self.loaded_buttons.append(transaction)
        buttons.addWidget(remove)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(transaction)
        buttons.addWidget(export)
        if self.live_mode:
            import_button = QPushButton("Import")
            import_button.setToolTip(
                "Write the queued changes through SCEWIN. A backup and a "
                "verification pass run around the import."
            )
            import_button.clicked.connect(self.apply_changes)
            self.loaded_buttons.append(import_button)
            buttons.addWidget(import_button)
        layout.addLayout(buttons)
        return tab

    def _compare_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        stock_row = QHBoxLayout()
        stock_row.addWidget(QLabel("NVRAM 1:"))
        self.stock_path_edit = QLineEdit()
        self.stock_path_edit.setReadOnly(True)
        self.stock_path_edit.setPlaceholderText("Select the first nvram.txt export")
        stock_row.addWidget(self.stock_path_edit, 1)
        stock_button = QPushButton("Browse...")
        stock_button.clicked.connect(lambda: self.load_compare_file("stock"))
        stock_row.addWidget(stock_button)
        layout.addLayout(stock_row)

        oc_row = QHBoxLayout()
        oc_row.addWidget(QLabel("NVRAM 2:"))
        self.overclocked_path_edit = QLineEdit()
        self.overclocked_path_edit.setReadOnly(True)
        self.overclocked_path_edit.setPlaceholderText("Select the second nvram.txt export")
        oc_row.addWidget(self.overclocked_path_edit, 1)
        oc_button = QPushButton("Browse...")
        oc_button.clicked.connect(lambda: self.load_compare_file("overclocked"))
        oc_row.addWidget(oc_button)
        layout.addLayout(oc_row)

        self.compare_summary = QLabel(
            "Select NVRAM 1 and NVRAM 2. Changed settings are shown by default."
        )
        self.compare_summary.setWordWrap(True)
        self.compare_summary.setStyleSheet("font-weight: 600; padding: 4px;")
        layout.addWidget(self.compare_summary)

        controls = QHBoxLayout()
        self.compare_search = QLineEdit()
        self.compare_search.setPlaceholderText(
            "Search compared setting, value, token, offset, or status..."
        )
        self.compare_search.textChanged.connect(self.compare_filter.set_search)
        controls.addWidget(self.compare_search, 1)
        self.compare_mode = QComboBox()
        self.compare_mode.addItems(["Changed only", "All entries", "Same only"])
        self.compare_mode.currentTextChanged.connect(self.compare_filter.set_mode)
        controls.addWidget(self.compare_mode)
        layout.addLayout(controls)

        self.compare_table = QTableView()
        self.compare_table.setModel(self.compare_filter)
        self.compare_table.setSortingEnabled(False)
        self.compare_table.setSelectionBehavior(QTableView.SelectRows)
        self.compare_table.setAlternatingRowColors(True)
        header = self.compare_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        for column in (4, 5, 6, 7, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.compare_table, 1)

        buttons = QHBoxLayout()
        swap = QPushButton("Swap files")
        swap.clicked.connect(self.swap_compare_files)
        self.compare_export_button = QPushButton("Export comparison CSV")
        self.compare_export_button.setEnabled(False)
        self.compare_export_button.clicked.connect(self.export_comparison)
        buttons.addWidget(swap)
        buttons.addStretch(1)
        buttons.addWidget(self.compare_export_button)
        layout.addLayout(buttons)
        return tab

    def _log_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        intro = QLabel(
            "Catalog of every NVRAM the tool has opened. A new dated entry is added "
            "each time the tool starts and whenever another NVRAM export is loaded."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-weight: 600; padding: 4px;")
        layout.addWidget(intro)
        path_label = QLabel(f"Catalog file: {self.catalog.catalog_path}")
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(path_label)

        self.catalog_table = QTableView()
        self.catalog_table.setModel(self.catalog_model)
        self.catalog_table.setSortingEnabled(False)
        self.catalog_table.setSelectionBehavior(QTableView.SelectRows)
        self.catalog_table.setAlternatingRowColors(True)
        self.catalog_table.doubleClicked.connect(self.open_selected_nvram)
        header = self.catalog_table.horizontalHeader()
        for column in (0, 1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.catalog_table, 1)
        self.catalog_table.scrollToTop()

        hint = QLabel("Double-click a row to open the archived nvram.txt for that capture.")
        hint.setStyleSheet("color: #6b7280; padding: 2px;")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        open_nvram = QPushButton("Open NVRAM file")
        open_nvram.clicked.connect(self.open_selected_nvram)
        open_archive = QPushButton("Open NVRAM archive folder")
        open_archive.clicked.connect(self.open_archive_folder)
        refresh = QPushButton("Refresh catalog")
        refresh.clicked.connect(self.refresh_catalog)
        open_folder = QPushButton("Open catalog folder")
        open_folder.clicked.connect(self.open_log_folder)
        export = QPushButton("Export catalog CSV...")
        export.clicked.connect(self.export_catalog)
        buttons.addWidget(open_nvram)
        buttons.addWidget(open_archive)
        buttons.addWidget(refresh)
        buttons.addWidget(open_folder)
        buttons.addStretch(1)
        buttons.addWidget(export)
        layout.addLayout(buttons)
        return tab

    def _log(self, event: str, message: str):
        # Audit trail on disk (nvram.log). The Log tab shows the NVRAM catalog.
        self.logger.write(event, message)

    def _catalog_capture(
        self,
        source: str,
        path: Any,
        hii_crc32: str,
        settings: int,
        sha256: str,
        amisce_version: str = "",
    ):
        entry = self.catalog.record(
            source,
            path,
            hii_crc32,
            settings,
            sha256,
            amisce_version=amisce_version,
            archive_source=path,
        )
        if self.catalog_model is not None:
            self.catalog_model.add_entry(entry)
        if self.catalog_table is not None:
            self.catalog_table.scrollToTop()

    def _log_nvram_open(self, source: str):
        self._log(
            "NVRAM_OPEN",
            f"{source}; path={self.document.path}; HII={self.document.hii_crc32}; "
            f"settings={len(self.profile.settings)}; SHA256={self.document.source_sha256}",
        )
        self._catalog_capture(
            source,
            self.document.path,
            self.document.hii_crc32,
            len(self.profile.settings),
            self.document.source_sha256,
            amisce_version=str(self.profile.metadata.get("amisce_version") or ""),
        )

    def open_selected_nvram(self, *_):
        if self.catalog_table is None:
            return
        index = self.catalog_table.currentIndex()
        if not index.isValid():
            QMessageBox.information(
                self, "Select a capture", "Select a catalogued NVRAM row first."
            )
            return
        entry = self.catalog_model.entry_at(index.row())
        if not entry.archived_path or not Path(entry.archived_path).is_file():
            QMessageBox.warning(
                self,
                "No archived NVRAM",
                "No archived nvram.txt was stored for this capture.",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(entry.archived_path)):
            QMessageBox.warning(
                self, "Could not open file", f"Archived NVRAM:\n{entry.archived_path}"
            )

    def open_archive_folder(self):
        self.catalog.archive_dir.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.catalog.archive_dir))):
            QMessageBox.warning(
                self, "Could not open folder", f"NVRAM archive:\n{self.catalog.archive_dir}"
            )

    def refresh_catalog(self):
        self.catalog_model.set_entries(self.catalog.entries())
        if self.catalog_table is not None:
            self.catalog_table.scrollToTop()

    def open_log_folder(self):
        self.catalog.catalog_dir.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.catalog.catalog_dir))):
            QMessageBox.warning(
                self, "Could not open folder", f"Catalog folder:\n{self.catalog.catalog_dir}"
            )

    def export_catalog(self):
        suggested = Path.cwd() / "exports" / "nvram_catalog.csv"
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export NVRAM catalog", str(suggested), "CSV files (*.csv)"
        )
        if not filename:
            return
        try:
            output = self.catalog.export_csv(filename)
        except OSError as exc:
            QMessageBox.critical(self, "Catalog export failed", str(exc))
            return
        QMessageBox.information(self, "Catalog exported", f"Created:\n{output}")

    def load_compare_file(self, role: str):
        title = (
            "Select NVRAM 1 nvram.txt"
            if role == "stock"
            else "Select NVRAM 2 nvram.txt"
        )
        filename, _ = QFileDialog.getOpenFileName(
            self, title, "", "SCEWIN NVRAM files (*.txt);;All files (*.*)"
        )
        if not filename:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            snapshot = load_snapshot(filename)
        except BiosManagerError as exc:
            QMessageBox.critical(self, "NVRAM comparison load failed", str(exc))
            role_label = "NVRAM 1" if role == "stock" else "NVRAM 2"
            self._log("COMPARE_OPEN_FAILED", f"source={role_label}; path={filename}; error={exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        if role == "stock":
            self.stock_snapshot = snapshot
            self.stock_path_edit.setText(str(snapshot.path))
            label = "NVRAM 1"
        else:
            self.overclocked_snapshot = snapshot
            self.overclocked_path_edit.setText(str(snapshot.path))
            label = "NVRAM 2"

        self._log(
            "NVRAM_OPEN",
            f"Compare {label}; path={snapshot.path}; HII={snapshot.hii_crc32}; "
            f"settings={len(snapshot.settings)}; SHA256={snapshot.sha256}",
        )
        self._catalog_capture(
            f"Compare {label}",
            snapshot.path,
            snapshot.hii_crc32,
            len(snapshot.settings),
            snapshot.sha256,
            amisce_version=snapshot.amisce_version,
        )
        if self.stock_snapshot is not None and self.overclocked_snapshot is not None:
            self._run_comparison()
        else:
            self.compare_summary.setText(
                f"Loaded {label} ({len(snapshot.settings):,} settings, "
                f"HII {snapshot.hii_crc32}). Select the other file to compare."
            )

    def _run_comparison(self):
        if self.stock_snapshot is None or self.overclocked_snapshot is None:
            return
        result = compare_snapshots(self.stock_snapshot, self.overclocked_snapshot)
        self.compare_result = result
        self.compare_model.set_rows(result.rows)
        self.compare_filter.invalidateFilter()
        self.compare_export_button.setEnabled(True)

        mismatch = ""
        if not result.hii_match:
            mismatch = (
                f" WARNING: HII CRC differs ({result.stock.hii_crc32} vs "
                f"{result.overclocked.hii_crc32}); offsets may not describe the same BIOS build."
            )
            self.compare_summary.setStyleSheet(
                "font-weight: 600; padding: 4px; color: #b45309;"
            )
        else:
            self.compare_summary.setStyleSheet("font-weight: 600; padding: 4px;")

        self.compare_summary.setText(
            f"Compared {len(result.rows):,} records: {result.changed_count:,} changed or "
            f"missing, {result.same_count:,} unchanged. HII CRC: "
            f"{result.stock.hii_crc32}.{mismatch}"
        )
        self._log(
            "COMPARE_COMPLETE",
            f"nvram1={result.stock.path}; nvram2={result.overclocked.path}; "
            f"changed={result.changed_count}; unchanged={result.same_count}; "
            f"HII_match={result.hii_match}",
        )

    def swap_compare_files(self):
        self.stock_snapshot, self.overclocked_snapshot = (
            self.overclocked_snapshot,
            self.stock_snapshot,
        )
        self.stock_path_edit.setText(
            str(self.stock_snapshot.path) if self.stock_snapshot is not None else ""
        )
        self.overclocked_path_edit.setText(
            str(self.overclocked_snapshot.path)
            if self.overclocked_snapshot is not None
            else ""
        )
        if self.stock_snapshot is not None and self.overclocked_snapshot is not None:
            self._run_comparison()
            self._log("COMPARE_FILES_SWAPPED", "NVRAM 1 and NVRAM 2 were swapped.")

    def export_comparison(self):
        if self.compare_result is None:
            QMessageBox.information(
                self, "No comparison", "Load both NVRAM files first."
            )
            return
        suggested = Path.cwd() / "exports" / "nvram_changed_settings.csv"
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export changed NVRAM settings", str(suggested), "CSV files (*.csv)"
        )
        if not filename:
            return
        try:
            output = export_compare_csv(
                filename, self.compare_result, changed_only=True
            )
        except BiosManagerError as exc:
            QMessageBox.critical(self, "Comparison export failed", str(exc))
            return
        self._log(
            "COMPARE_EXPORTED",
            f"Exported {self.compare_result.changed_count} changed rows to {output}.",
        )
        QMessageBox.information(
            self,
            "Comparison exported",
            f"Exported {self.compare_result.changed_count} changed setting(s):\n{output}",
        )

    def _update_profile_line(self):
        if self.profile is None or self.document is None:
            self.profile_line.setText(
                "No NVRAM loaded   |   press Export to read the live settings"
            )
            return
        self.profile_line.setText(
            f"{self.profile.label}   |   HII CRC32 {self.document.hii_crc32}   |   "
            f"{len(self.profile.settings):,} settings"
        )

    def _update_loaded_actions(self):
        """Enable the actions that need an NVRAM in the table."""
        loaded = self.profile is not None and self.document is not None
        for button in self.loaded_buttons:
            button.setEnabled(loaded)

    def _nvram_loaded(self, action: str) -> bool:
        if self.profile is not None and self.document is not None:
            return True
        QMessageBox.information(
            self,
            "No NVRAM loaded",
            f"{action} needs an NVRAM export.\n\n"
            + (
                "Press Export to read the live settings first."
                if self.live_mode
                else "Open an export and profile from the File menu first."
            ),
        )
        return False

    @contextmanager
    def _busy(self, message: str):
        """Disable the window and show a wait cursor while SCEWIN runs.

        The window is restored on the way out, so it is already usable by the
        time a caller reports an error: a message box is never raised over a
        disabled window, and no cursor is left pushed if the call throws.
        """
        self.setEnabled(False)
        self.statusBar().showMessage(message)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()
            self.setEnabled(True)
            self._update_loaded_actions()
            self._update_status()
            QApplication.processEvents()

    def _show_nvram(self, profile: ParsedProfile, document: ScewinDocument, source: str):
        """Put a freshly opened export in the table and catalogue it."""
        self.profile = profile
        self.document = document
        self.changes.clear()
        self.settings_model.beginResetModel()
        self.settings_model.settings = profile.settings
        self.settings_model.endResetModel()
        self.change_model.refresh()
        self.filter_model.invalidateFilter()
        self._update_profile_line()
        self._update_status()
        self._update_loaded_actions()
        self._log_nvram_open(source)

    def _confirm_discard_queue(self, reason: str) -> bool:
        if not self.changes:
            return True
        answer = QMessageBox.question(
            self,
            "Discard queued changes?",
            f"{reason}\n\n{len(self.changes)} queued change(s) will be cleared. "
            "Nothing has been written to firmware.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _run_live_export(self, tag: str) -> LiveExport | None:
        """Read the live NVRAM through SCEWIN, the way SCEWIN's Export.bat does."""
        if not self.live_mode or self.backend is None:
            QMessageBox.information(
                self, "Dry run", "Reading the live NVRAM is disabled in offline mode."
            )
            return None
        try:
            with self._busy("Reading the live NVRAM through SCEWIN..."):
                return self.backend.export_current(tag)
        except BiosManagerError as exc:
            self._log("NVRAM_EXPORT_FAILED", f"tag={tag}; error={exc}")
            QMessageBox.critical(self, "NVRAM export failed", str(exc))
            return None

    def export_live_nvram(self):
        """Read the live NVRAM on demand. Nothing touches firmware until this runs."""
        if not self._confirm_discard_queue(
            "Reading the live NVRAM replaces the table with a fresh export."
        ):
            return
        live = self._run_live_export("current")
        if live is None:
            return
        self._show_nvram(live.profile, live.document, "Export NVRAM")
        QMessageBox.information(
            self,
            "NVRAM exported",
            f"Read {len(live.profile.settings):,} settings from the live NVRAM.\n\n"
            f"{live.nvram_path}",
        )

    def apply_changes(self):
        if not self.live_mode or self.backend is None:
            QMessageBox.information(self, "Dry run", "Import is disabled in offline mode.")
            return
        if not self._nvram_loaded("Import"):
            return
        if not self.changes:
            QMessageBox.information(
                self, "No changes", "Queue at least one setting change first."
            )
            return

        self._log(
            "APPLY_PREFLIGHT",
            f"Starting preflight for {len(self.changes)} queued change(s).",
        )
        try:
            with self._busy("Refreshing live NVRAM before import..."):
                plan = self.backend.prepare_apply(self.profile, self.changes.values())
        except BiosManagerError as exc:
            self._log("APPLY_PREFLIGHT_FAILED", str(exc))
            QMessageBox.critical(self, "Import preflight failed", str(exc))
            return

        lines = [f"Import {len(plan.changes)} queued NVRAM change(s)?", ""]
        for change in plan.changes[:12]:
            lines.append(f"• {change.question}: {change.old_display} → {change.new_display}")
        if len(plan.changes) > 12:
            lines.append(f"• ...and {len(plan.changes) - 12} more")
        lines.extend(
            [
                "",
                "A full NVRAM backup and transaction log will be created before import.",
                "Incorrect firmware settings can prevent booting. A reboot may be required.",
            ]
        )
        answer = QMessageBox.question(
            self,
            "Confirm NVRAM import",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self._log("APPLY_CANCELLED", "User cancelled the confirmed NVRAM import.")
            return

        for change in plan.changes:
            self._log(
                "APPLY_REQUESTED_CHANGE",
                f"{change.question}: {change.old_display} -> {change.new_display}; "
                f"token={change.token_hex}; offset={change.offset_hex}; width={change.width_hex}",
            )

        try:
            with self._busy("Importing queued NVRAM settings through SCEWIN..."):
                result: ApplyResult = self.backend.execute_apply(plan)
        except BiosManagerError as exc:
            self._log("APPLY_FAILED", str(exc))
            QMessageBox.critical(self, "NVRAM import not verified", str(exc))
            return

        self._log(
            "APPLY_SUCCESS",
            f"Imported and verified {result.verified_count} change(s); backup={result.backup_dir}",
        )
        self._show_nvram(result.verified_export.profile, result.verified_export.document, "Live refresh after apply")
        QMessageBox.information(
            self,
            "NVRAM changes applied",
            f"All {result.verified_count} queued change(s) were imported and verified.\n\n"
            f"Backup: {result.backup_dir}\n\n"
            "Reboot Windows for settings that require restart.",
        )

    def _source_setting(self) -> dict[str, Any] | None:
        index = self.settings_table.currentIndex()
        if not index.isValid():
            return None
        source_index = self.filter_model.mapToSource(index)
        return self.settings_model.setting_at(source_index.row())

    def edit_selected(self, *_):
        if not self._nvram_loaded("Queueing a change"):
            return
        setting = self._source_setting()
        if not setting:
            QMessageBox.information(self, "Select a setting", "Select an NVRAM setting first.")
            return
        editable, reason = self.document.is_editable(setting)
        if not editable:
            QMessageBox.warning(
                self,
                "Setting blocked",
                f"This entry is not editable in the safe build:\n\n{reason}",
            )
            return
        dialog = EditDialog(
            setting, self.document, self.profile, self, live_mode=self.live_mode
        )
        if dialog.exec() and dialog.change:
            # Compare raw values, not the displayed labels: two option codes may
            # carry the same label, and comparing labels silently discarded a
            # real change (and any change already queued for the setting).
            if dialog.change.new_raw == raw_value(setting):
                removed = self.changes.pop(dialog.change.export_id, None)
                if removed is not None:
                    self._log(
                        "CHANGE_REMOVED",
                        f"{removed.question}: queued change was reset to the current value.",
                    )
            else:
                self.changes[dialog.change.export_id] = dialog.change
                self._log(
                    "CHANGE_QUEUED",
                    f"{dialog.change.question}: {dialog.change.old_display} -> "
                    f"{dialog.change.new_display}; token={dialog.change.token_hex}; "
                    f"offset={dialog.change.offset_hex}; width={dialog.change.width_hex}",
                )
            self._refresh_models()

    def _choose_saved_nvram(self, title: str) -> NvramSnapshot | None:
        start_dir = str(self.catalog.archive_dir) if self.catalog.archive_dir.is_dir() else ""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            title,
            start_dir,
            "SCEWIN NVRAM files (*.txt);;All files (*.*)",
        )
        if not filename:
            return None
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            return load_snapshot(filename)
        except BiosManagerError as exc:
            QMessageBox.critical(self, "Could not load NVRAM", str(exc))
            self._log("NVRAM_LOAD_FAILED", f"path={filename}; error={exc}")
            return None
        finally:
            QApplication.restoreOverrideCursor()

    def _differences_from(self, snapshot: NvramSnapshot) -> SavedNvramDiff:
        """Match a saved nvram.txt to the live export by token/offset/width.

        The saved file is never written wholesale. Every difference becomes a
        normal pending change, so the preflight, backup, and verification path
        still applies to everything Load NVRAM queues.
        """
        current_by_identity = {
            identity_without_crc(setting): setting for setting in self.profile.settings
        }
        queued: list[PendingChange] = []
        blocked: list[str] = []
        missing = 0
        already = 0
        seen: set[str] = set()

        for saved in snapshot.settings:
            key = identity_without_crc(saved)
            if not has_identity(key) or key in seen:
                continue
            seen.add(key)
            current = current_by_identity.get(key)
            name = str(saved.get("question") or "N/A")
            if current is None:
                missing += 1
                continue
            if saved.get("kind") != current.get("kind"):
                blocked.append(f"{name}: type differs from the live export")
                continue
            if raw_value(saved) == raw_value(current):
                already += 1
                continue
            editable, reason = self.document.is_editable(current)
            if not editable:
                blocked.append(f"{name}: {reason}")
                continue
            try:
                queued.append(
                    self.document.build_change(
                        self.profile, str(current["export_id"]), raw_value(saved)
                    )
                )
            except BiosManagerError as exc:
                blocked.append(f"{name}: {exc}")

        return SavedNvramDiff(queued, blocked, missing, already)

    def _report_no_differences(self, snapshot: NvramSnapshot, diff: SavedNvramDiff, title: str):
        QMessageBox.information(
            self,
            title,
            "No applicable differences were found.\n\n"
            f"{diff.already:,} setting(s) already match the live NVRAM.\n"
            f"{diff.missing:,} were not found in the live export.\n"
            f"{len(diff.blocked):,} could not be queued.",
        )
        self._log(
            "NVRAM_LOAD_EMPTY",
            f"path={snapshot.path}; already_match={diff.already}; missing={diff.missing}; "
            f"skipped={len(diff.blocked)}",
        )

    def load_nvram(self):
        """Queue the differences from a saved nvram.txt (e.g. after a CMOS clear).

        Nothing is written: the differences land in Pending Changes for review,
        and Import performs the write.
        """
        if not self._nvram_loaded("Load NVRAM"):
            return
        snapshot = self._choose_saved_nvram("Load saved NVRAM export")
        if snapshot is None:
            return
        diff = self._differences_from(snapshot)
        queued, blocked, missing, already = diff
        if not queued:
            self._report_no_differences(snapshot, diff, "Nothing to restore")
            return

        lines = [f"Queue {len(queued):,} setting(s) from:", str(snapshot.path), ""]
        if snapshot.hii_crc32 != self.document.hii_crc32:
            lines.extend(
                [
                    f"WARNING: HII CRC differs (saved {snapshot.hii_crc32} vs live "
                    f"{self.document.hii_crc32}).",
                    "Settings are matched by token/offset/width instead.",
                    "",
                ]
            )
        for change in queued[:12]:
            lines.append(f"• {change.question}: {change.old_display} → {change.new_display}")
        if len(queued) > 12:
            lines.append(f"• ...and {len(queued) - 12:,} more")
        lines.extend(
            [
                "",
                f"{already:,} already match, {missing:,} not in live export, "
                f"{len(blocked):,} skipped.",
                "",
                "Nothing is written yet. Review under Pending Changes, then press Import.",
            ]
        )
        answer = QMessageBox.question(
            self,
            "Load NVRAM",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self._log("NVRAM_LOAD_CANCELLED", f"path={snapshot.path}; candidates={len(queued)}")
            return

        for change in queued:
            self.changes[change.export_id] = change
        self._log(
            "NVRAM_LOAD_QUEUED",
            f"path={snapshot.path}; queued={len(queued)}; already_match={already}; "
            f"missing={missing}; skipped={len(blocked)}; saved_HII={snapshot.hii_crc32}; "
            f"live_HII={self.document.hii_crc32}",
        )
        self._refresh_models()
        QMessageBox.information(
            self,
            "NVRAM loaded",
            f"Queued {len(queued):,} change(s) from the saved NVRAM.\n\n"
            "Review them under Pending Changes, then press Import to write them.",
        )

    def remove_change(self):
        index = self.change_table.currentIndex()
        if not index.isValid():
            return
        change = self.change_model.items()[index.row()]
        self.changes.pop(change.export_id, None)
        self._log(
            "CHANGE_REMOVED",
            f"{change.question}: removed queued {change.old_display} -> {change.new_display}.",
        )
        self._refresh_models()

    def clear_changes(self):
        count = len(self.changes)
        self.changes.clear()
        if count:
            self._log("CHANGES_CLEARED", f"Cleared {count} queued change(s).")
        self._refresh_models()

    def _refresh_models(self):
        self.settings_model.refresh()
        self.change_model.refresh()
        self._update_status()

    def _update_status(self):
        backup_text = (
            f"   |   Backups: {self.backend.backup_root}"
            if self.backend is not None
            else ""
        )
        source_text = (
            f"Source SHA-256: {self.document.source_sha256[:16]}…"
            if self.document is not None
            else "No NVRAM loaded"
        )
        self.statusBar().showMessage(
            f"{source_text}   |   Queued changes: {len(self.changes)}{backup_text}"
        )

    def export_nvram(self):
        if not self._nvram_loaded("Exporting a modified nvram.txt"):
            return
        if not self.changes:
            QMessageBox.information(self, "No changes", "Queue at least one setting change first.")
            return
        suggested = Path.cwd() / "exports" / "nvram_modified_dry_run.txt"
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export modified SCEWIN file", str(suggested), "Text files (*.txt)"
        )
        if not filename:
            return
        try:
            output = self.document.write_modified_copy(filename, self.changes.values())
        except BiosManagerError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._log(
            "NVRAM_EXPORT_CREATED",
            f"Created modified export {output} with {len(self.changes)} queued change(s).",
        )
        QMessageBox.information(
            self,
            "Dry-run export created",
            f"Created:\n{output}\n\nThe original nvram.txt was not changed.",
        )

    def export_transaction(self):
        if not self._nvram_loaded("Exporting a transaction"):
            return
        if not self.changes:
            QMessageBox.information(self, "No changes", "Queue at least one setting change first.")
            return
        suggested = Path.cwd() / "exports" / "bios_transaction_dry_run.json"
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export transaction", str(suggested), "JSON files (*.json)"
        )
        if not filename:
            return
        try:
            output = write_transaction(
                filename, self.profile, self.document, self.changes.values()
            )
        except BiosManagerError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._log(
            "TRANSACTION_EXPORTED",
            f"Created transaction {output} with {len(self.changes)} queued change(s).",
        )
        QMessageBox.information(self, "Transaction created", f"Created:\n{output}")

    def open_pair(self):
        nvram, _ = QFileDialog.getOpenFileName(
            self, "Select original SCEWIN nvram.txt", "", "Text files (*.txt)"
        )
        if not nvram:
            return
        profile, _ = QFileDialog.getOpenFileName(
            self, "Select matching parsed profile JSON", "", "JSON files (*.json)"
        )
        if not profile:
            return
        try:
            new_profile = ParsedProfile.load(profile)
            new_document = ScewinDocument(nvram)
            new_document.verify_profile(new_profile)
        except BiosManagerError as exc:
            self._log(
                "NVRAM_OPEN_FAILED",
                f"path={nvram}; profile={profile}; error={exc}",
            )
            QMessageBox.critical(self, "Profile mismatch", str(exc))
            return

        self._show_nvram(new_profile, new_document, "Manual open")
        QMessageBox.information(
            self,
            "NVRAM loaded",
            f"Loaded {len(self.profile.settings):,} settings from:\n{self.document.path}",
        )


def run(
    profile_path: Path,
    nvram_path: Path,
    project_root: Path | None = None,
    live: bool = True,
) -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    backend: ScewinBackend | None = None

    if live:
        # SCEWIN is not run here. The window opens with nothing loaded and reads
        # the firmware only when Export is pressed.
        backend = ScewinBackend(project_root or Path(__file__).resolve().parents[1])
    else:
        absent = [str(path) for path in (profile_path, nvram_path) if not Path(path).is_file()]
        if absent:
            QMessageBox.critical(
                None,
                "Sample NVRAM not found",
                "Offline mode needs a sample export, which is not shipped because it "
                "is board-specific.\n\nMissing:\n"
                + "\n".join(absent)
                + "\n\nPoint the app at your own export with --profile and --nvram.",
            )
            return 1

    # The launchers start pythonw.exe, which has no console: anything that escapes
    # here would take the app down with no window and no message at all.
    try:
        window = MainWindow(
            None if live else profile_path,
            None if live else nvram_path,
            backend=backend,
        )
    except BiosManagerError as exc:
        QMessageBox.critical(
            None,
            "NVRAM could not be opened",
            f"{APP_NAME} could not open the NVRAM export. "
            f"Nothing was written.\n\n{exc}",
        )
        return 1
    except Exception as exc:
        QMessageBox.critical(
            None,
            "Unexpected error",
            f"{APP_NAME} could not start.\n\n{type(exc).__name__}: {exc}",
        )
        return 1

    window.show()
    return app.exec()
