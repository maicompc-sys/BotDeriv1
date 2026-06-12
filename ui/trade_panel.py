"""
Trade Panel - Histórico, controles manuais e operações abertas
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, QGroupBox,
    QDoubleSpinBox, QSpinBox, QComboBox, QSplitter, QTabWidget, QTextEdit,
    QGridLayout, QFrame)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont
from bot.database import get_trades, get_strategy_stats
from bot.deriv_api import SYNTHETIC_SYMBOLS, GRANULARITIES, TICK_SIZES
from datetime import datetime


def _safe_float(val, default=0.0) -> float:
    """Converte valor do DB para float com segurança (None → default)."""
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _safe_str(val, default="─") -> str:
    """Converte valor do DB para string com segurança (None → default)."""
    return str(val) if val is not None else default


class TradeHistoryTable(QTableWidget):
    def __init__(self):
        super().__init__()
        cols = ["ID", "Símbolo", "Estratégia", "Tipo", "Stake", "Payout", "Lucro", "Resultado", "Entrada", "Duração"]
        self.setColumnCount(len(cols))
        self.setHorizontalHeaderLabels(cols)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setMinimumHeight(200)

    def load_trades(self, trades: list):
        self.setRowCount(len(trades))
        for r, t in enumerate(trades):
            # FIX: todos os campos numéricos protegidos contra None/NULL do DB
            profit  = _safe_float(t.get("profit"))
            stake   = _safe_float(t.get("stake"))
            payout  = _safe_float(t.get("payout"))
            result  = _safe_str(t.get("result"), "─")
            color = QColor("#00c853") if result == "win" else QColor("#ff1744") if result == "loss" else QColor("#8b99b4")
            vals = [
                _safe_str(t.get("id")),
                _safe_str(t.get("symbol")),
                _safe_str(t.get("strategy")),
                _safe_str(t.get("contract_type")),
                f"${stake:.2f}",
                f"${payout:.2f}",
                f"${profit:+.2f}",
                result.upper(),
                _safe_str(t.get("entry_time"), "─")[:19],
                _safe_str(t.get("duration")),
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if c in (6, 7):
                    item.setForeground(color)
                self.setItem(r, c, item)


class OpenTradesTable(QTableWidget):
    def __init__(self):
        super().__init__()
        cols = ["Contract ID", "Símbolo", "Tipo", "Stake", "Expira em", "Status"]
        self.setColumnCount(len(cols))
        self.setHorizontalHeaderLabels(cols)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setMinimumHeight(120)

    def update_contracts(self, contracts: list):
        self.setRowCount(len(contracts))
        for r, c in enumerate(contracts):
            vals = [
                _safe_str(c.get("contract_id")),
                _safe_str(c.get("underlying")),
                _safe_str(c.get("contract_type")),
                f"${_safe_float(c.get('buy_price')):.2f}",
                _safe_str(c.get("date_expiry")),
                _safe_str(c.get("status")),
            ]
            for ci, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self.setItem(r, ci, item)


class StatsWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QGridLayout(self)
        layout.setSpacing(8)
        self._vals = {}
        metrics = [
            ("total_trades", "Total Trades"), ("wins", "Vitórias"), ("losses", "Derrotas"),
            ("win_rate", "Win Rate"), ("total_profit", "Lucro Total"), ("avg_profit", "Lucro Médio"),
            ("best_strategy", "Melhor Estratégia"), ("daily_pnl", "PnL Hoje"),
        ]
        for i, (key, label) in enumerate(metrics):
            r, c = divmod(i, 2)
            frame = QFrame()
            frame.setStyleSheet("QFrame{background:#111520;border:1px solid #1e2d40;border-radius:4px;}")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(8, 6, 8, 6)
            lbl = QLabel(label)
            lbl.setStyleSheet("color:#4a6080; font-size:10px; font-weight:600; letter-spacing:0.3px;")
            val = QLabel("─")
            val.setStyleSheet("color:#c0d0e8; font-size:14px; font-weight:700;")
            self._vals[key] = val
            fl.addWidget(lbl)
            fl.addWidget(val)
            layout.addWidget(frame, r, c)

    def update_stats(self, trades: list):
        if not trades:
            return
        closed = [t for t in trades if t.get("status") == "closed" or t.get("result")]
        wins   = [t for t in closed if t.get("result") == "win"]
        losses = [t for t in closed if t.get("result") == "loss"]
        total_profit = sum(_safe_float(t.get("profit")) for t in closed)
        win_rate  = len(wins) / len(closed) if closed else 0
        avg_profit = total_profit / len(closed) if closed else 0
        today = datetime.now().date().isoformat()
        today_trades = [t for t in closed if (_safe_str(t.get("entry_time"), "")).startswith(today)]
        daily_pnl = sum(_safe_float(t.get("profit")) for t in today_trades)
        stats_db = get_strategy_stats()
        best = max(stats_db, key=lambda x: x.get("win_rate", 0)) if stats_db else None
        wr_color   = "#00c853" if win_rate >= 0.7 else "#ffd700" if win_rate >= 0.5 else "#ff1744"
        pnl_color  = "#00c853" if total_profit >= 0 else "#ff1744"
        dpnl_color = "#00c853" if daily_pnl >= 0 else "#ff1744"
        self._vals["total_trades"].setText(str(len(closed)))
        self._vals["wins"].setText(str(len(wins)))
        self._vals["wins"].setStyleSheet("color:#00c853; font-size:14px; font-weight:700;")
        self._vals["losses"].setText(str(len(losses)))
        self._vals["losses"].setStyleSheet("color:#ff1744; font-size:14px; font-weight:700;")
        self._vals["win_rate"].setText(f"{win_rate*100:.1f}%")
        self._vals["win_rate"].setStyleSheet(f"color:{wr_color}; font-size:14px; font-weight:700;")
        self._vals["total_profit"].setText(f"${total_profit:+.2f}")
        self._vals["total_profit"].setStyleSheet(f"color:{pnl_color}; font-size:14px; font-weight:700;")
        self._vals["avg_profit"].setText(f"${avg_profit:+.2f}")
        self._vals["best_strategy"].setText(best["strategy"][:15] if best else "─")
        self._vals["best_strategy"].setStyleSheet("color:#00d4ff; font-size:12px; font-weight:700;")
        self._vals["daily_pnl"].setText(f"${daily_pnl:+.2f}")
        self._vals["daily_pnl"].setStyleSheet(f"color:{dpnl_color}; font-size:14px; font-weight:700;")


class TradePanel(QWidget):
    manual_buy = Signal(str, str, int, str, float)  # symbol, type, duration, unit, stake
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self.refresh_trades)
        self._refresh_timer.start(3000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        tabs = QTabWidget()

        # ── Tab 1: Manual Trade ──────────────────────────────────────────
        manual_tab = QWidget()
        ml = QVBoxLayout(manual_tab)
        ml.setSpacing(8)

        ctrl = QGroupBox("OPERAÇÃO MANUAL")
        cl = QGridLayout(ctrl)
        cl.setSpacing(6)

        cl.addWidget(QLabel("Símbolo:"), 0, 0)
        self.cmb_symbol = QComboBox()
        for name in SYNTHETIC_SYMBOLS:
            self.cmb_symbol.addItem(name, SYNTHETIC_SYMBOLS[name])
        cl.addWidget(self.cmb_symbol, 0, 1)

        cl.addWidget(QLabel("Duração:"), 1, 0)
        dur_row = QHBoxLayout()
        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(1, 300)
        self.spin_duration.setValue(5)
        self.cmb_duration_unit = QComboBox()
        self.cmb_duration_unit.addItems(["t (ticks)", "s (segundos)", "m (minutos)"])
        dur_row.addWidget(self.spin_duration)
        dur_row.addWidget(self.cmb_duration_unit)
        cl.addLayout(dur_row, 1, 1)

        cl.addWidget(QLabel("Stake (USD):"), 2, 0)
        self.spin_stake = QDoubleSpinBox()
        self.spin_stake.setRange(0.35, 50000)
        self.spin_stake.setValue(1.00)
        self.spin_stake.setDecimals(2)
        self.spin_stake.setSingleStep(0.5)
        cl.addWidget(self.spin_stake, 2, 1)

        btn_row = QHBoxLayout()
        self.btn_call = QPushButton("▲ COMPRAR (CALL)")
        self.btn_call.setObjectName("btn_call")
        self.btn_call.setMinimumHeight(44)
        self.btn_put = QPushButton("▼ VENDER (PUT)")
        self.btn_put.setObjectName("btn_put")
        self.btn_put.setMinimumHeight(44)
        self.btn_call.clicked.connect(lambda: self._do_manual("CALL"))
        self.btn_put.clicked.connect(lambda: self._do_manual("PUT"))
        btn_row.addWidget(self.btn_call)
        btn_row.addWidget(self.btn_put)

        ml.addWidget(ctrl)
        ml.addLayout(btn_row)

        self.stats_widget = StatsWidget()
        ml.addWidget(self.stats_widget)
        ml.addStretch()
        tabs.addTab(manual_tab, "🎯 Manual")

        # ── Tab 2: Trade History ─────────────────────────────────────────
        hist_tab = QWidget()
        hl = QVBoxLayout(hist_tab)
        refresh_btn = QPushButton("🔄 Atualizar")
        refresh_btn.clicked.connect(self.refresh_trades)
        hl.addWidget(refresh_btn)
        self.trade_table = TradeHistoryTable()
        hl.addWidget(self.trade_table)
        tabs.addTab(hist_tab, "📋 Histórico")

        # ── Tab 3: Open Positions ────────────────────────────────────────
        open_tab = QWidget()
        ol = QVBoxLayout(open_tab)
        self.open_table = OpenTradesTable()
        ol.addWidget(QLabel("Posições Abertas:"))
        ol.addWidget(self.open_table)
        tabs.addTab(open_tab, "📂 Abertas")

        layout.addWidget(tabs)

    def _do_manual(self, contract_type):
        symbol_data = self.cmb_symbol.currentData()
        unit_txt = self.cmb_duration_unit.currentText()
        unit = "t" if "t" in unit_txt else "s" if "s" in unit_txt else "m"
        self.manual_buy.emit(
            symbol_data, contract_type,
            self.spin_duration.value(), unit,
            self.spin_stake.value()
        )

    def refresh_trades(self):
        try:
            trades = get_trades(limit=100)
            self.trade_table.load_trades(trades)
            self.stats_widget.update_stats(trades)
        except Exception as e:
            pass  # Silencia erros de refresh para não travar a UI

    def update_open_contracts(self, contracts: list):
        self.open_table.update_contracts(contracts)
