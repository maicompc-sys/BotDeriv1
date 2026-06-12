"""
Painel de Estratégias - Controles e Status das 10 estratégias
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QGroupBox, QPushButton, QScrollArea, QFrame)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from bot.strategies import ALL_STRATEGIES
from bot.database import get_strategy_stats

class StrategyStatusRow(QFrame):
    toggled = Signal(str, bool)
    def __init__(self, strategy, parent=None):
        super().__init__(parent)
        self.strategy_name = strategy.name
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame { background: #111520; border: 1px solid #1e2d40; border-radius: 4px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        self.chk = QCheckBox(strategy.name)
        self.chk.setChecked(True)
        self.chk.stateChanged.connect(lambda s: self.toggled.emit(self.strategy_name, s == 2))
        self.lbl_signal = QLabel("─")
        self.lbl_signal.setStyleSheet("color:#4a6080; font-size:11px; min-width:50px;")
        self.lbl_conf = QLabel("0%")
        self.lbl_conf.setStyleSheet("color:#8b99b4; font-size:11px; min-width:40px;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(8)
        self.bar.setMaximumWidth(80)
        self.lbl_wr = QLabel("─%")
        self.lbl_wr.setStyleSheet("color:#8b99b4; font-size:10px; min-width:35px;")
        lay.addWidget(self.chk, stretch=2)
        lay.addWidget(self.lbl_signal)
        lay.addWidget(self.bar)
        lay.addWidget(self.lbl_conf)
        lay.addWidget(self.lbl_wr)

    def update_result(self, result: dict):
        sig = result.get("signal")
        conf = result.get("confidence", 0)
        if sig == "CALL":
            self.lbl_signal.setText("▲ CALL")
            self.lbl_signal.setStyleSheet("color:#00c853; font-size:11px; font-weight:700; min-width:50px;")
            self.bar.setStyleSheet("QProgressBar::chunk{background:#00c853;}")
        elif sig == "PUT":
            self.lbl_signal.setText("▼ PUT")
            self.lbl_signal.setStyleSheet("color:#ff1744; font-size:11px; font-weight:700; min-width:50px;")
            self.bar.setStyleSheet("QProgressBar::chunk{background:#ff1744;}")
        else:
            self.lbl_signal.setText("─")
            self.lbl_signal.setStyleSheet("color:#4a6080; font-size:11px; min-width:50px;")
            self.bar.setStyleSheet("")
        self.bar.setValue(int(conf))
        self.lbl_conf.setText(f"{conf:.0f}%")

    def update_winrate(self, wr: float):
        self.lbl_wr.setText(f"{wr*100:.0f}%")
        color = "#00c853" if wr >= 0.7 else "#ffd700" if wr >= 0.5 else "#ff1744"
        self.lbl_wr.setStyleSheet(f"color:{color}; font-size:10px; min-width:35px;")


class StrategyPanel(QWidget):
    strategies_changed = Signal(list)  # list of enabled strategy names

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = {s.name: True for s in ALL_STRATEGIES}
        self._rows = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        lbl = QLabel("ESTRATÉGIAS ATIVAS")
        lbl.setStyleSheet("color:#00d4ff; font-size:12px; font-weight:700; letter-spacing:1px;")
        btn_all = QPushButton("Todas ON")
        btn_all.setMaximumWidth(80)
        btn_all.clicked.connect(self._enable_all)
        btn_none = QPushButton("Todas OFF")
        btn_none.setMaximumWidth(80)
        btn_none.clicked.connect(self._disable_all)
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(btn_all)
        hdr.addWidget(btn_none)
        layout.addLayout(hdr)

        # Column headers
        col_hdr = QHBoxLayout()
        for txt, width in [("ESTRATÉGIA", 140), ("SINAL", 50), ("CONF", 80), ("%", 35), ("WR", 35)]:
            l = QLabel(txt)
            l.setStyleSheet("color:#4a6080; font-size:10px; font-weight:600; letter-spacing:0.3px;")
            l.setMinimumWidth(width)
            col_hdr.addWidget(l)
        layout.addLayout(col_hdr)

        # Strategy rows in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(3)
        inner_lay.setContentsMargins(0,0,0,0)
        for strat in ALL_STRATEGIES:
            row = StrategyStatusRow(strat)
            row.toggled.connect(self._on_toggle)
            self._rows[strat.name] = row
            inner_lay.addWidget(row)
        inner_lay.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        # Consensus display
        grp = QGroupBox("CONSENSO")
        grp_lay = QVBoxLayout(grp)
        self.lbl_consensus = QLabel("Aguardando dados...")
        self.lbl_consensus.setStyleSheet("color:#8b99b4; font-size:12px;")
        self.lbl_consensus.setAlignment(Qt.AlignCenter)
        self.lbl_votes = QLabel("")
        self.lbl_votes.setStyleSheet("color:#4a6080; font-size:11px;")
        self.lbl_votes.setAlignment(Qt.AlignCenter)
        grp_lay.addWidget(self.lbl_consensus)
        grp_lay.addWidget(self.lbl_votes)
        layout.addWidget(grp)

    def _on_toggle(self, name, enabled):
        self._enabled[name] = enabled
        self.strategies_changed.emit(self.get_enabled())

    def _enable_all(self):
        for name, row in self._rows.items():
            self._enabled[name] = True
            row.chk.setChecked(True)
        self.strategies_changed.emit(self.get_enabled())

    def _disable_all(self):
        for name, row in self._rows.items():
            self._enabled[name] = False
            row.chk.setChecked(False)
        self.strategies_changed.emit(self.get_enabled())

    def get_enabled(self):
        return [name for name, en in self._enabled.items() if en]

    def update_results(self, results: list):
        for r in results:
            name = r.get("strategy", "")
            if name in self._rows:
                self._rows[name].update_result(r)

    def update_consensus(self, consensus: dict):
        sig = consensus.get("signal")
        conf = consensus.get("confidence", 0)
        calls = consensus.get("votes_call", 0)
        puts = consensus.get("votes_put", 0)
        if sig == "CALL":
            self.lbl_consensus.setText(f"🟢 CONSENSO: CALL  {conf:.0f}%")
            self.lbl_consensus.setStyleSheet("color:#00c853; font-size:14px; font-weight:700;")
        elif sig == "PUT":
            self.lbl_consensus.setText(f"🔴 CONSENSO: PUT   {conf:.0f}%")
            self.lbl_consensus.setStyleSheet("color:#ff1744; font-size:14px; font-weight:700;")
        else:
            self.lbl_consensus.setText("⏳ Sem consenso")
            self.lbl_consensus.setStyleSheet("color:#4a6080; font-size:12px; font-style:italic;")
        self.lbl_votes.setText(f"▲ {calls} estratégias CALL  |  ▼ {puts} estratégias PUT")

    def refresh_winrates(self):
        stats = get_strategy_stats()
        for s in stats:
            name = s.get("strategy","")
            wr = s.get("win_rate", 0)
            if name in self._rows:
                self._rows[name].update_winrate(wr)
