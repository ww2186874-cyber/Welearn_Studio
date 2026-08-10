"""WeLearn Studio process entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from welearn_studio import __version__
from welearn_studio.application import StudioController
from welearn_studio.services.settings import JsonSettingsStore, SettingsError
from welearn_studio.ui import MainWindow


def settings_path_from_environment() -> Path:
    configured = os.environ.get("WELEARN_STUDIO_SETTINGS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return StudioController.default_settings_path()


def should_restore_last_account_file() -> bool:
    value = os.environ.get("WELEARN_STUDIO_NO_RESTORE", "").strip().casefold()
    return value not in {"1", "true", "yes", "on"}


def main() -> int:
    QCoreApplication.setOrganizationName("WeLearn Studio")
    QCoreApplication.setApplicationName("WeLearn Studio")
    QCoreApplication.setApplicationVersion(__version__)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    try:
        settings = JsonSettingsStore(settings_path_from_environment())
    except SettingsError:
        QMessageBox.critical(None, "启动失败", "本地配置文件无法读取，请检查文件内容。")
        return 1
    workspace = settings.load_workspace()
    window = MainWindow(initial_scale=workspace.interface_scale_percent)
    controller = StudioController(
        window,
        settings=settings,
        restore_last_file=should_restore_last_account_file(),
    )
    window._studio_controller = controller
    app.aboutToQuit.connect(controller.shutdown)
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
