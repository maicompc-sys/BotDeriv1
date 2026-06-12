DARK_STYLE = """
QMainWindow, QWidget {
    background-color: #0d0f14;
    color: #e2e8f0;
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 12px;
}
QTabWidget::pane {
    border: 1px solid #1e2d40;
    background: #111520;
    border-radius: 4px;
}
QTabBar::tab {
    background: #151a24; color: #8b99b4;
    padding: 8px 16px;
    border: 1px solid #1e2d40; border-bottom: none;
    border-radius: 4px 4px 0 0;
    font-size: 11px; font-weight: 500;
}
QTabBar::tab:selected { background: #1a2235; color: #00d4ff; border-color: #00d4ff; }
QTabBar::tab:hover:!selected { background: #1a2235; color: #c0d0e8; }
QGroupBox {
    border: 1px solid #1e2d40; border-radius: 6px;
    margin-top: 8px; padding-top: 8px;
    background: #111520; color: #8b99b4;
    font-size: 11px; font-weight: 600; letter-spacing: 0.5px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; top: -1px;
    color: #00d4ff; background: #0d0f14; padding: 0 4px;
}
QPushButton {
    background: #1a2235; color: #c0d0e8;
    border: 1px solid #2a3a55; border-radius: 4px;
    padding: 6px 14px; font-size: 12px; font-weight: 500;
}
QPushButton:hover { background: #1e2a3e; border-color: #00d4ff; color: #00d4ff; }
QPushButton:pressed { background: #162030; }
QPushButton#btn_call, QPushButton#btn_start {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #00c853,stop:1 #009624);
    color: white; border: none; font-weight: 700; font-size: 13px;
}
QPushButton#btn_call:hover, QPushButton#btn_start:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #00e65c,stop:1 #00a82b);
}
QPushButton#btn_put, QPushButton#btn_stop {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #ff1744,stop:1 #c41230);
    color: white; border: none; font-weight: 700; font-size: 13px;
}
QPushButton#btn_put:hover, QPushButton#btn_stop:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #ff4569,stop:1 #d32f4f);
}
QComboBox {
    background: #151a24; color: #c0d0e8;
    border: 1px solid #2a3a55; border-radius: 4px;
    padding: 4px 8px; min-width: 100px;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox:hover { border-color: #00d4ff; }
QComboBox QAbstractItemView {
    background: #151a24; color: #c0d0e8;
    border: 1px solid #2a3a55;
    selection-background-color: #1a3a55;
}
QSpinBox, QDoubleSpinBox, QLineEdit {
    background: #151a24; color: #c0d0e8;
    border: 1px solid #2a3a55; border-radius: 4px; padding: 4px 8px;
}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: #00d4ff; }
QTableWidget {
    background: #0d0f14; color: #c0d0e8;
    border: 1px solid #1e2d40; gridline-color: #1e2d40;
    alternate-background-color: #111520; border-radius: 4px;
}
QTableWidget::item { padding: 4px 8px; }
QTableWidget::item:selected { background: #1a3a55; color: #fff; }
QHeaderView::section {
    background: #151a24; color: #8b99b4;
    border: none; border-right: 1px solid #1e2d40;
    border-bottom: 1px solid #1e2d40;
    padding: 6px 8px; font-size: 11px; font-weight: 600;
}
QScrollBar:vertical { background: #0d0f14; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #2a3a55; border-radius: 4px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #00d4ff; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel#label_price {
    color: #00d4ff; font-size: 28px; font-weight: 700;
    font-family: 'Consolas', monospace;
}
QLabel#label_balance { color: #00ff88; font-size: 16px; font-weight: 700; }
QFrame#tick_frame {
    background: #0a0c12; border: 1px solid #1e2d40; border-radius: 8px;
}
QProgressBar {
    background: #151a24; border: 1px solid #2a3a55; border-radius: 3px;
    text-align: center; color: #fff; font-size: 10px; font-weight: 600; height: 14px;
}
QProgressBar::chunk { background: #00d4ff; border-radius: 2px; }
QProgressBar#bar_call::chunk { background: #00c853; }
QProgressBar#bar_put::chunk  { background: #ff1744; }
QCheckBox { color: #8b99b4; spacing: 6px; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid #2a3a55; border-radius: 3px; background: #151a24;
}
QCheckBox::indicator:checked { background: #00d4ff; border-color: #00d4ff; }
QTextEdit {
    background: #0a0c12; color: #8b99b4;
    border: 1px solid #1e2d40; border-radius: 4px;
    font-family: 'Consolas', monospace; font-size: 11px;
}
QSplitter::handle { background: #1e2d40; width: 2px; }
"""