#!/usr/bin/env python3
"""Standalone dialogs used by the main window: a string-list editor and the dry-run preview."""

from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)


class StringListDialog(QDialog):
    """Editor for a list of strings (used for ignore patterns and post-deploy commands).

    Values are only read back through values() when the dialog is accepted, and
    blank lines are dropped there rather than blocked on entry.
    """

    def __init__(self, title: str, label: str, items: list[str], parent=None, monospace: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(480, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        self.list_widget = QListWidget()
        for item in items:
            self.list_widget.addItem(item)
        if monospace:
            font = QFont("Consolas", 10)
            font.setStyleHint(QFont.StyleHint.Monospace)
            self.list_widget.setFont(font)
        layout.addWidget(self.list_widget)
        # Add / edit / remove act on the current or selected rows.
        row = QHBoxLayout()
        add_button = QPushButton("Add...")
        add_button.clicked.connect(self.add_item)
        edit_button = QPushButton("Edit selected")
        edit_button.clicked.connect(self.edit_selected)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self.remove_selected)
        row.addWidget(add_button)
        row.addWidget(edit_button)
        row.addWidget(remove_button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_item(self) -> None:
        text, ok = QInputDialog.getText(self, "Add entry", "Value:")
        if ok and text.strip():
            self.list_widget.addItem(text.strip())

    def edit_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        text, ok = QInputDialog.getText(self, "Edit entry", "Value:", text=item.text())
        if ok:
            item.setText(text.strip())

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def values(self) -> list[str]:
        """The list as typed, minus anything that is only whitespace."""
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count()) if self.list_widget.item(i).text().strip()]


class DryRunDialog(QDialog):
    """Read-only report of what a deploy would change, built from a dry-run plan.

    The plan comes straight from SftpWorker.deploy() in dry_run mode, so nothing
    here has to re-derive anything - it only counts and lists.
    """

    def __init__(self, plan: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deploy preview (dry run)")
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        unchanged = plan.get("unchanged", [])
        # Headline numbers first; the deletion lines only appear if that mirror
        # direction is actually switched on.
        summary = (
            f"Directories to create: {len(plan['directories'])}\n"
            f"Files to upload (changed): {len(plan['files'])}\n"
        )
        if unchanged:
            summary += f"Unchanged files skipped: {len(unchanged)}\n"
        if plan["would_delete"]:
            summary += f"Server files to remove (not on local): {len(plan['stale_files'])}\n"
            summary += f"Server directories to remove (not on local): {len(plan['stale_dirs'])}\n"
        if plan.get("would_delete_local"):
            summary += f"Local files to remove (not on server): {len(plan.get('local_files', []))}\n"
            summary += f"Local directories to remove (not on server): {len(plan.get('local_dirs', []))}\n"
        if not plan["would_delete"] and not plan.get("would_delete_local"):
            summary += "Mirror cleanup is OFF - nothing will be deleted."
        label = QLabel(summary)
        label.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(label)

        # The delete tabs stay visible but empty when mirror cleanup is off.
        tabs = QTabWidget()
        tabs.addTab(self._make_list(plan["directories"]), "New / update dirs")
        tabs.addTab(self._make_list(plan["files"]), "Files to upload")
        if unchanged:
            tabs.addTab(self._make_list(unchanged), "Unchanged (skipped)")
        tabs.addTab(self._make_list(plan["stale_files"] + plan["stale_dirs"]), "Delete on server")
        tabs.addTab(
            self._make_list(plan.get("local_files", []) + plan.get("local_dirs", [])),
            "Delete on local",
        )
        layout.addWidget(tabs, 1)

        note = QLabel("This is a preview. No changes were made. Close this dialog and press Deploy to apply.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #ffb020;")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _make_list(items: list[str]) -> QListWidget:
        """A read-only sorted list box for one tab of the preview."""
        widget = QListWidget()
        for item in sorted(items, key=lambda entry: entry.lower()):
            widget.addItem(item)
        return widget
