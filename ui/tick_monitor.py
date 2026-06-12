"""
Monitor de Ticks Central - Componente Principal da UI
Exibe preço ao vivo, histórico de ticks, mini chart
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QGridLayout, QSizePolicy, QProgressBar)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, Property, QRect
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QLinearGradient, QBrush, QPainterPath
from collections import deque
import numpy as np  # FIX: movido para topo — era importado a cada tick
import time

class SparklineWidget(QWidget):
    """Mini gráfico de linha para histórico de preços"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.prices = deque(maxlen=120)
        self.signal = None  # "CALL", "PUT", None
        self.setMinimumHeight(80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def add_price(self, price):
        self.prices.append(price)
        self.update()

    def set_signal(self, signal):
        self.signal = signal
        self.update()

    def paintEvent(self, event):
        if len(self.prices) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pad = 8
        prices = list(self.prices)
        mn, mx = min(prices), max(prices)
        rng = mx - mn if mx != mn else 0.0001

        def px(i): return pad + (i / (len(prices)-1)) * (w - 2*pad)
        def py(p): return pad + (1 - (p - mn) / rng) * (h - 2*pad)

        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor("#0a0c12"))
        bg.setColorAt(1, QColor("#080a10"))
        painter.fillRect(0, 0, w, h, bg)

        pen = QPen(QColor("#1e2d40"), 1, Qt.DotLine)
        painter.setPen(pen)
        for i in range(1, 4):
            y = h * i // 4
            painter.drawLine(0, y, w, y)

        if self.signal == "CALL":
            fill_color = QColor(0, 200, 83, 30)
            line_color = QColor("#00c853")
        elif self.signal == "PUT":
            fill_color = QColor(255, 23, 68, 30)
            line_color = QColor("#ff1744")
        else:
            fill_color = QColor(0, 212, 255, 20)
            line_color = QColor("#00d4ff")

        path = QPainterPath()
        path.moveTo(px(0), h)
        path.lineTo(px(0), py(prices[0]))
        for i in range(1, len(prices)):
            path.lineTo(px(i), py(prices[i]))
        path.lineTo(px(len(prices)-1), h)
        path.closeSubpath()
        painter.fillPath(path, fill_color)

        pen = QPen(line_color, 2)
        painter.setPen(pen)
        for i in range(1, len(prices)):
            painter.drawLine(int(px(i-1)), int(py(prices[i-1])), int(px(i)), int(py(prices[i])))

        last_x, last_y = int(px(len(prices)-1)), int(py(prices[-1]))
        painter.setBrush(line_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(last_x - 4, last_y - 4, 8, 8)

        painter.end()


class TickBarWidget(QWidget):
    """Barras de ticks individuais (histórico visual)"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ticks = deque(maxlen=60)
        self.setMinimumHeight(50)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def add_tick(self, price, prev_price):
        direction = "up" if price >= prev_price else "down"
        self.ticks.append((price, direction))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor("#0a0c12"))
        if not self.ticks:
            painter.end()
            return
        n = len(self.ticks)
        bar_w = max(4, (w - 4) // max(n, 1))
        for i, (price, direction) in enumerate(self.ticks):
            x = 2 + i * bar_w
            color = QColor("#00c853") if direction == "up" else QColor("#ff1744")
            painter.fillRect(x, 8, bar_w - 1, h - 16, color)
        painter.end()


class TickMonitorWidget(QFrame):
    """Widget central de monitoramento de ticks"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tick_frame")
        self.current_price = 0.0
        self.prev_price = 0.0
        self.tick_count = 0
        self.prices = deque(maxlen=200)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        hdr = QHBoxLayout()
        self.lbl_symbol = QLabel("─ Selecione um símbolo ─")
        self.lbl_symbol.setStyleSheet("color:#8b99b4; font-size:12px; font-weight:600; letter-spacing:1px;")
        self.lbl_status = QLabel("● DESCONECTADO")
        self.lbl_status.setStyleSheet("color:#ff1744; font-size:10px; font-weight:600;")
        hdr.addWidget(self.lbl_symbol)
        hdr.addStretch()
        hdr.addWidget(self.lbl_status)
        layout.addLayout(hdr)

        price_row = QHBoxLayout()
        self.lbl_price = QLabel("─────")
        self.lbl_price.setObjectName("label_price")
        self.lbl_price.setAlignment(Qt.AlignCenter)
        self.lbl_arrow = QLabel("─")
        self.lbl_arrow.setStyleSheet("font-size:24px; font-weight:700;")
        self.lbl_change = QLabel("")
        self.lbl_change.setStyleSheet("font-size:13px; font-weight:600;")
        price_row.addStretch()
        price_row.addWidget(self.lbl_arrow)
        price_row.addWidget(self.lbl_price)
        price_row.addWidget(self.lbl_change)
        price_row.addStretch()
        layout.addLayout(price_row)

        stats = QGridLayout()
        stats.setSpacing(8)
        self._stat_labels = {}
        for i, (key, lbl) in enumerate([
            ("high", "HIGH"), ("low", "LOW"), ("spread", "SPREAD"),
            ("ticks", "TICKS"), ("vol", "VOLATIL"), ("trend", "TENDÊNCIA")
        ]):
            col_lbl = QLabel(lbl)
            col_lbl.setStyleSheet("color:#4a6080; font-size:10px; font-weight:600; letter-spacing:0.5px;")
            col_lbl.setAlignment(Qt.AlignCenter)
            val_lbl = QLabel("─")
            val_lbl.setStyleSheet("color:#c0d0e8; font-size:12px; font-weight:600;")
            val_lbl.setAlignment(Qt.AlignCenter)
            self._stat_labels[key] = val_lbl
            stats.addWidget(col_lbl, 0, i)
            stats.addWidget(val_lbl, 1, i)
        layout.addLayout(stats)

        self.sparkline = SparklineWidget()
        layout.addWidget(self.sparkline, stretch=3)

        self.tick_bars = TickBarWidget()
        layout.addWidget(self.tick_bars)

        sig_row = QHBoxLayout()
        self.lbl_signal = QLabel("Aguardando sinal...")
        self.lbl_signal.setStyleSheet("color:#4a6080; font-size:11px; font-style:italic;")
        self.lbl_signal.setAlignment(Qt.AlignCenter)
        sig_row.addWidget(self.lbl_signal)
        layout.addLayout(sig_row)

        conf_row = QHBoxLayout()
        self.lbl_conf_call = QLabel("CALL")
        self.lbl_conf_call.setStyleSheet("color:#00c853; font-size:10px; font-weight:700; min-width:35px;")
        self.lbl_conf_put = QLabel("PUT")
        self.lbl_conf_put.setStyleSheet("color:#ff1744; font-size:10px; font-weight:700; min-width:35px; qproperty-alignment:AlignRight;")
        self.bar_call = QProgressBar()
        self.bar_call.setObjectName("bar_call")
        self.bar_call.setRange(0, 100)
        self.bar_call.setValue(0)
        self.bar_call.setTextVisible(True)
        self.bar_put = QProgressBar()
        self.bar_put.setObjectName("bar_put")
        self.bar_put.setRange(0, 100)
        self.bar_put.setValue(0)
        self.bar_put.setTextVisible(True)
        conf_row.addWidget(self.lbl_conf_call)
        conf_row.addWidget(self.bar_call)
        conf_row.addWidget(self.bar_put)
        conf_row.addWidget(self.lbl_conf_put)
        layout.addLayout(conf_row)

    def update_tick(self, symbol: str, price: float):
        self.lbl_symbol.setText(symbol)
        self.prev_price = self.current_price
        self.current_price = price
        self.prices.append(price)
        self.tick_count += 1

        if len(self.prices) < 2:
            return

        if price > self.prev_price:
            arrow, color = "▲", "#00c853"
        elif price < self.prev_price:
            arrow, color = "▼", "#ff1744"
        else:
            arrow, color = "─", "#8b99b4"

        change = price - self.prev_price if self.prev_price else 0
        self.lbl_price.setText(f"{price:.5f}")
        self.lbl_price.setStyleSheet(f"color:{color}; font-size:28px; font-weight:700; font-family:'Consolas',monospace;")
        self.lbl_arrow.setText(arrow)
        self.lbl_arrow.setStyleSheet(f"color:{color}; font-size:24px; font-weight:700;")
        chg_txt = f"+{change:.5f}" if change >= 0 else f"{change:.5f}"
        self.lbl_change.setText(chg_txt)
        self.lbl_change.setStyleSheet(f"color:{color}; font-size:13px; font-weight:600;")

        prices_list = list(self.prices)
        if prices_list:
            hi = max(prices_list[-50:]) if len(prices_list) >= 50 else max(prices_list)
            lo = min(prices_list[-50:]) if len(prices_list) >= 50 else min(prices_list)
            # FIX: np já importado no topo do arquivo
            vol = np.std(prices_list[-20:]) if len(prices_list) >= 20 else 0
            trend = "ALTA ▲" if prices_list[-1] > np.mean(prices_list[-10:]) else "BAIXA ▼"
            trend_color = "#00c853" if "ALTA" in trend else "#ff1744"
            self._stat_labels["high"].setText(f"{hi:.4f}")
            self._stat_labels["high"].setStyleSheet("color:#00c853; font-size:12px; font-weight:600;")
            self._stat_labels["low"].setText(f"{lo:.4f}")
            self._stat_labels["low"].setStyleSheet("color:#ff1744; font-size:12px; font-weight:600;")
            self._stat_labels["spread"].setText(f"{(hi-lo):.5f}")
            self._stat_labels["spread"].setStyleSheet("color:#c0d0e8; font-size:12px; font-weight:600;")
            self._stat_labels["ticks"].setText(str(self.tick_count))
            self._stat_labels["ticks"].setStyleSheet("color:#00d4ff; font-size:12px; font-weight:600;")
            self._stat_labels["vol"].setText(f"{vol:.5f}")
            self._stat_labels["vol"].setStyleSheet("color:#ffd700; font-size:12px; font-weight:600;")
            self._stat_labels["trend"].setText(trend)
            self._stat_labels["trend"].setStyleSheet(f"color:{trend_color}; font-size:12px; font-weight:600;")

        self.sparkline.add_price(price)
        if self.prev_price:
            self.tick_bars.add_tick(price, self.prev_price)

    def update_signal(self, signal, call_conf, put_conf):
        if signal == "CALL":
            self.lbl_signal.setText(f"🟢 SINAL: CALL  |  Confiança: {call_conf:.0f}%")
            self.lbl_signal.setStyleSheet("color:#00c853; font-size:13px; font-weight:700;")
            self.sparkline.set_signal("CALL")
        elif signal == "PUT":
            self.lbl_signal.setText(f"🔴 SINAL: PUT   |  Confiança: {put_conf:.0f}%")
            self.lbl_signal.setStyleSheet("color:#ff1744; font-size:13px; font-weight:700;")
            self.sparkline.set_signal("PUT")
        else:
            self.lbl_signal.setText("⏳ Aguardando sinal...")
            self.lbl_signal.setStyleSheet("color:#4a6080; font-size:11px; font-style:italic;")
            self.sparkline.set_signal(None)
        self.bar_call.setValue(int(call_conf))
        self.bar_put.setValue(int(put_conf))

    def set_connected(self, ok):
        if ok:
            self.lbl_status.setText("● CONECTADO")
            self.lbl_status.setStyleSheet("color:#00c853; font-size:10px; font-weight:600;")
        else:
            self.lbl_status.setText("● DESCONECTADO")
            self.lbl_status.setStyleSheet("color:#ff1744; font-size:10px; font-weight:600;")
