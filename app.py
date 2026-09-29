from __future__ import annotations

import os
import sys

if (
    sys.platform.startswith("linux")
    and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    and os.environ.get("DISPLAY")
):
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PyQt6.QtWidgets import QApplication

from frontend import Controller


def main() -> int:
    app = QApplication(sys.argv)
    controller = Controller()
    app.aboutToQuit.connect(controller.shutdown)
    controller.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
