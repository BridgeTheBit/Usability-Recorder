from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app_paths import get_resource_path
from gui import RecorderWidget
from settings_server import settings_server


def main() -> None:
    # Start the local settings web panel server (background thread)
    settings_server.start()

    app = QApplication(sys.argv)
    app.setApplicationName("Atlas")
    app.setOrganizationName("BridgeTheBit")
    app.setWindowIcon(QIcon(str(get_resource_path("assets", "atlas-icon.png"))))

    window = RecorderWidget()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
