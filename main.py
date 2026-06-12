#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPixmap, QColor, QPainter, QLinearGradient

def create_splash():
    pm = QPixmap(600, 300)
    painter = QPainter(pm)
    grad = QLinearGradient(0, 0, 600, 300)
    grad.setColorAt(0, QColor("#0d0f14"))
    grad.setColorAt(1, QColor("#111520"))
    painter.fillRect(0, 0, 600, 300, grad)
    painter.setPen(QColor("#00d4ff"))
    font = QFont("Segoe UI", 28, QFont.Bold)
    painter.setFont(font)
    painter.drawText(0, 0, 600, 180, Qt.AlignCenter, "⚡ DERIV BOT")
    painter.setPen(QColor("#8b99b4"))
    font2 = QFont("Segoe UI", 11)
    painter.setFont(font2)
    painter.drawText(0, 160, 600, 60, Qt.AlignCenter, "Institutional Trading System | R_ Sintéticos")
    painter.setPen(QColor("#4a6080"))
    font3 = QFont("Segoe UI", 9)
    painter.setFont(font3)
    painter.drawText(0, 230, 600, 40, Qt.AlignCenter, "Maicom Jordan dos Santos Danone")
    painter.drawText(0, 255, 600, 40, Qt.AlignCenter, "Inicializando 10 estratégias...")
    painter.end()
    splash = QSplashScreen(pm)
    splash.setWindowFlag(Qt.WindowStaysOnTopHint)
    return splash

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Deriv Institutional Bot")
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    splash = create_splash()
    splash.show()
    app.processEvents()
    from ui.main_window import MainWindow
    window = MainWindow()
    def show_main():
        splash.finish(window)
        window.show()
    QTimer.singleShot(1800, show_main)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()