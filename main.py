from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gui import RecorderWidget
from session_controller import SessionController
from settings_server import settings_server


def main() -> None:
    # Start the local settings web panel server (background thread)
    settings_server.start()

    app = QApplication(sys.argv)
    app.setApplicationName("Usability Recorder")

    controller = SessionController()
    window = RecorderWidget(controller)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
