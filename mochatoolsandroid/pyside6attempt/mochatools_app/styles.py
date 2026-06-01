# styles.py — Mocha Tools stylesheet (Android edition)
# Touch targets bumped to 44–48px per Android guidelines.
# DropZone drag_active pseudo-state removed (no drag & drop on mobile).
# Tab padding increased for easier tapping.

STYLESHEET = """
QMainWindow, QWidget#root {
    background-color: #111010;
}
QWidget {
    background-color: transparent;
    color: #f0ece6;
    font-family: "Segoe UI", "SF Pro Display", "Inter", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QFrame#card {
    background-color: #181614;
    border: 1px solid #2e2b27;
    border-radius: 10px;
    padding: 4px;
}
QLabel#section_header {
    color: #5a5650;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 0px;
    background: transparent;
}
QLabel#field_label {
    color: #9c9484;
    font-size: 12px;
    min-width: 90px;
    background: transparent;
}
QLabel#status_label {
    color: #f0ece6;
    font-size: 12px;
    background: transparent;
}
QLineEdit {
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    padding: 0px 10px;
    color: #f0ece6;
    font-size: 15px;
    selection-background-color: #c8a96e;
    min-height: 44px;
    max-height: 44px;
}
QLineEdit:focus {
    border: 1px solid #c8a96e;
    background-color: #222018;
}
QLineEdit::placeholder { color: #5a5650; }
QSpinBox {
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    padding: 6px 8px;
    color: #f0ece6;
    font-size: 13px;
}
QSpinBox:focus { border-color: #c8a96e; }
QSpinBox::up-button, QSpinBox::down-button { background: #252320; border: none; width: 18px; }
QSpinBox::up-arrow   { border: 4px solid transparent; border-bottom: 5px solid #5a5650; width:0; height:0; }
QSpinBox::down-arrow { border: 4px solid transparent; border-top:    5px solid #5a5650; width:0; height:0; }
QComboBox {
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    padding: 6px 10px;
    color: #f0ece6;
    font-size: 13px;
}
QComboBox:focus { border-color: #c8a96e; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { border: 4px solid transparent; border-top: 5px solid #5a5650; width:0; height:0; }
QComboBox QAbstractItemView {
    background-color: #1e1c19;
    border: 1px solid #3d3a35;
    border-radius: 8px;
    selection-background-color: #c8a96e33;
    selection-color: #f0ece6;
    outline: none;
}
QPushButton#upload_btn {
    background: #c8a96e;
    color: #111010;
    border: none;
    border-radius: 8px;
    padding: 14px 24px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.3px;
    min-height: 48px;
}
QPushButton#upload_btn:hover   { background: #d4b87a; }
QPushButton#upload_btn:pressed { background: #a88950; }
QPushButton#upload_btn:disabled { background: #2e2b27; color: #5a5650; }
QPushButton#browse_btn {
    background-color: #1e1c19;
    color: #c8a96e;
    border: 1px solid #c8a96e44;
    border-radius: 8px;
    padding: 0px 14px;
    font-size: 12px;
    min-height: 48px;
    max-height: 48px;
}
QPushButton#browse_btn:hover { background-color: #252320; border-color: #c8a96e88; }
QCheckBox {
    color: #9c9484;
    font-size: 12px;
    spacing: 6px;
    background: transparent;
}
QCheckBox::indicator {
    width: 20px;
    height: 20px;
    border: 1px solid #3d3a35;
    border-radius: 4px;
    background: #1e1c19;
}
QCheckBox::indicator:checked { background: #c8a96e; border-color: #c8a96e; image: none; }
QCheckBox::indicator:hover   { border-color: #c8a96e; }
QProgressBar {
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 5px;
    height: 6px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a88950, stop:1 #d4b87a);
    border-radius: 5px;
}
QLabel#log_console {
    background-color: #141210;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    color: #c8a96e;
    font-family: "Consolas", "Fira Code", "Courier New", monospace;
    font-size: 11px;
    padding: 8px 10px;
    min-height: 46px;
}
QLabel#status_badge {
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 10px;
    color: #9c9484;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 10px;
}
QFrame#drop_zone {
    background-color: #141210;
    border: 2px dashed #2e2b27;
    border-radius: 12px;
    min-height: 90px;
}
QLabel#drop_label      { color: #5a5650; font-size: 13px; background: transparent; }
QLabel#drop_label_bold { color: #c8a96e; font-size: 13px; font-weight: 700; background: transparent; }
QFrame#divider { background-color: #2e2b27; max-height: 1px; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; }
QScrollBar::handle:vertical { background: #3d3a35; border-radius: 3px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab {
    background: #181614;
    color: #9c9484;
    border: 1px solid #2e2b27;
    border-bottom: none;
    border-radius: 8px 8px 0 0;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 600;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #111010; color: #c8a96e; border-bottom: 2px solid #c8a96e; }
QTabBar::tab:hover:!selected { background: #1e1c19; color: #f0ece6; }
QTreeWidget {
    background: #141210;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    color: #f0ece6;
    font-size: 12px;
    outline: none;
    show-decoration-selected: 1;
}
QTreeWidget::item { padding: 5px 4px; border-bottom: 1px solid #1a1816; }
QTreeWidget::item:selected { background: #c8a96e22; color: #f0ece6; }
QTreeWidget::item:hover:!selected { background: #1e1c19; }
QHeaderView::section {
    background: #1e1c19;
    color: #5a5650;
    border: none;
    border-right: 1px solid #2e2b27;
    border-bottom: 1px solid #2e2b27;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QPushButton#tb_btn {
    background: #1e1c19;
    color: #c8a96e;
    border: 1px solid #2e2b27;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
    min-height: 44px;
}
QPushButton#tb_btn:hover    { background: #252320; border-color: #c8a96e55; }
QPushButton#tb_btn:pressed  { background: #141210; }
QPushButton#tb_btn:disabled { color: #5a5650; border-color: #1e1c19; }
QPushButton#tb_btn_danger {
    background: #1e1c19;
    color: #f87171;
    border: 1px solid #2e2b27;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
    min-height: 44px;
}
QPushButton#tb_btn_danger:hover    { background: #251a1a; border-color: #f8717155; }
QPushButton#tb_btn_danger:disabled { color: #5a5650; border-color: #1e1c19; }
QMenu {
    background: #1e1c19;
    border: 1px solid #3d3a35;
    border-radius: 8px;
    color: #f0ece6;
    font-size: 12px;
}
QMenu::item { padding: 6px 24px; border-radius: 4px; }
QMenu::item:selected { background: #c8a96e33; }
QMenu::separator { height: 1px; background: #2e2b27; margin: 4px 8px; }
QDialog { background-color: #181614; }
QDialogButtonBox QPushButton {
    min-width: 72px;
    min-height: 44px;
    border-radius: 7px;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 16px;
    background: #1e1c19;
    color: #f0ece6;
    border: 1px solid #3d3a35;
}
QDialogButtonBox QPushButton:hover  { background: #252320; }
QDialogButtonBox QPushButton#upload_btn {
    background: #c8a96e;
    color: #111010;
    border: none;
}
QDialogButtonBox QPushButton#upload_btn:hover { background: #d4b87a; }
QListWidget {
    background: #141210;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    color: #f0ece6;
    font-size: 13px;
}
QListWidget::item { padding: 10px 10px; }
QListWidget::item:selected { background: #c8a96e33; color: #f0ece6; }
QListWidget::item:hover { background: #1e1c19; }
QMessageBox { background-color: #181614; }
QMessageBox QLabel { color: #f0ece6; background: transparent; }
QPushButton {
    background: #1e1c19;
    color: #f0ece6;
    border: 1px solid #3d3a35;
    border-radius: 7px;
    padding: 6px 16px;
    min-height: 44px;
}
QPushButton:hover  { background: #252320; }
QPushButton:pressed { background: #141210; }
"""
