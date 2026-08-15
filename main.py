from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gui import SessionWindow
from session_controller import SessionController


def main() -> None:
    app = QApplication(sys.argv)
    controller = SessionController()
    window = SessionWindow(controller)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
