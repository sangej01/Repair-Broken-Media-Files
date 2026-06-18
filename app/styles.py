"""Dark theme stylesheet for Repair Broken Media Files GUI."""

DARK_THEME = """
QMainWindow, QDialog {
    background-color: #0a0a12;
    color: #e8e8e8;
}

QLabel {
    color: #e8e8e8;
    font-size: 13px;
}

QLabel#heading {
    font-size: 22px;
    font-weight: 700;
    color: #00e5ff;
}

QLabel#subheading {
    font-size: 13px;
    color: #888da0;
}

QLabel#progressText {
    font-size: 15px;
    font-weight: 600;
    color: #00e676;
    padding: 4px 0;
}

QLabel#summary {
    font-size: 14px;
    font-weight: 600;
    color: #00ff88;
    padding: 8px 0;
}

QLabel#statusLabel {
    font-size: 13px;
    font-weight: 600;
    color: #888da0;
    padding: 4px 0;
}

QPushButton {
    background-color: #1c1c2e;
    color: #e8e8e8;
    border: 1px solid #2e2e48;
    border-radius: 6px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #2a2a44;
    border-color: #3e3e5c;
}

QPushButton:pressed {
    background-color: #36365a;
}

QPushButton:disabled {
    background-color: #0e0e18;
    color: #3a3a50;
    border-color: #1c1c2e;
}

QPushButton:checked {
    background-color: #00e5ff;
    color: #000000;
    border: 1px solid #00e5ff;
}

QPushButton:checked:hover {
    background-color: #18ffff;
}

QPushButton#primary {
    background-color: #00c853;
    color: #000000;
    border: none;
}

QPushButton#primary:hover {
    background-color: #00e676;
}

QPushButton#primary:pressed {
    background-color: #00b848;
}

QPushButton#primary:disabled {
    background-color: #1c1c2e;
    color: #3a3a50;
}

QPushButton#danger {
    background-color: #e53935;
    color: #ffffff;
    border: none;
}

QPushButton#danger:hover {
    background-color: #ff1744;
}

QPushButton#danger:pressed {
    background-color: #c62828;
}

QPushButton#danger:disabled {
    background-color: #1c1c2e;
    color: #3a3a50;
}

QLineEdit {
    background-color: #14142a;
    color: #e8e8e8;
    border: 1px solid #2e2e48;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    selection-background-color: #00e5ff;
    selection-color: #000000;
}

QLineEdit:focus {
    border-color: #00e5ff;
}

QComboBox {
    background-color: #1c1c2e;
    color: #e8e8e8;
    border: 1px solid #2e2e48;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
    font-weight: 600;
}

QComboBox:hover {
    border-color: #00e5ff;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #00e5ff;
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    background-color: #14142a;
    color: #e8e8e8;
    border: 1px solid #2e2e48;
    selection-background-color: #00e5ff;
    selection-color: #000000;
    padding: 4px;
}

QProgressBar {
    background-color: #14142a;
    border: none;
    border-radius: 4px;
    height: 10px;
    text-align: center;
    color: #e8e8e8;
    font-size: 11px;
}

QProgressBar::chunk {
    background-color: #00e5ff;
    border-radius: 4px;
}

QSpinBox {
    background-color: #1c1c2e;
    color: #e8e8e8;
    border: 1px solid #2e2e48;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
}

QSpinBox:hover {
    border-color: #00e5ff;
}

QTableWidget {
    background-color: #0e0e1a;
    alternate-background-color: #121222;
    color: #e8e8e8;
    border: 1px solid #1c1c2e;
    border-radius: 8px;
    gridline-color: #1c1c2e;
    selection-background-color: #1a3a5c;
    selection-color: #e8e8e8;
    font-size: 12px;
}

QTableWidget::item {
    padding: 6px 10px;
}

QHeaderView::section {
    background-color: #14142a;
    color: #00e5ff;
    border: none;
    border-bottom: 2px solid #00e5ff;
    padding: 8px 10px;
    font-weight: 600;
    font-size: 12px;
}

QScrollBar:vertical {
    background-color: #0a0a12;
    width: 10px;
    border: none;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background-color: #2e2e48;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background-color: #00e5ff;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
    border: none;
}

QScrollBar:horizontal {
    background-color: #0a0a12;
    height: 10px;
    border: none;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background-color: #2e2e48;
    border-radius: 5px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #00e5ff;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
    border: none;
}

QCheckBox {
    spacing: 8px;
    color: #e8e8e8;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #2e2e48;
    border-radius: 4px;
    background-color: #14142a;
}

QCheckBox::indicator:checked {
    background-color: #00e5ff;
    border-color: #00e5ff;
}

QCheckBox::indicator:hover {
    border-color: #00e5ff;
}

QMessageBox {
    background-color: #0a0a12;
    color: #e8e8e8;
}

QMessageBox QLabel {
    color: #e8e8e8;
}

QMessageBox QPushButton {
    min-width: 80px;
}
"""
