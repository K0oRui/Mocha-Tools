# main.py
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from mochatools_app.app_android import MochaTools
from mochatools_app.styles import STYLESHEET
from mochatools_app.constants import APP_NAME, ORG_NAME


def main():
    # On Android, argv is managed by Qt — don't pass sys.argv directly
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    # Fusion style works on Android
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    win = MochaTools()
    win.showMaximized()   # Android apps always fill the screen
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
