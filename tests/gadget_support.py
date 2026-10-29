"""Build a real Gadget against temp settings/database for offscreen UI tests."""
from pathlib import Path
import time
from unittest.mock import patch

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from llama_gadget.app import Gadget

APPLICATION = QApplication.instance() or QApplication([])


def pump(seconds=0.0):
    deadline = time.monotonic() + seconds
    APPLICATION.processEvents()
    while time.monotonic() < deadline:
        time.sleep(.005)
        APPLICATION.processEvents()


def make_gadget(test, root, profile='agent-gpu0'):
    root = Path(root)
    settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
    settings.setValue('db_path', str(root / 'chat.db'))
    settings.setValue('profile', profile)
    with patch('llama_gadget.app.QSettings', return_value=settings), patch.object(Gadget, '_poll'):
        gadget = Gadget()
    gadget.timer.stop()

    def cleanup():
        gadget._shutdown_workers()
        gadget.tray.hide()
        gadget.clipboard.dataChanged.disconnect(gadget._on_clipboard)
        gadget.db.conn.close()
        gadget.hide()
        gadget.deleteLater()
        APPLICATION.processEvents()
    test.addCleanup(cleanup)
    return gadget
