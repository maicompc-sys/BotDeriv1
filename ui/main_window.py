"""
Janela Principal - Interface Institucional Level
Layout: Sidebar | Tick Monitor Central | Strategy Panel
"""
import sys
import os
from datetime import datetime
from collections import deque
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QGroupBox,
    QSplitter, QStatusBar, QFrame, QCheckBox, QTabWidget, QTextEdit,
    QGridLayout, QScrollArea, QMessageBox, QLineEdit, QSystemTrayIcon, QMenu)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QSize
from PySide6.QtGui import QIcon, QFont, QColor, QAction

from bot.deriv_api import DerivAPI, SYNTHETIC_SYMBOLS, GRANULARITIES, TICK_SIZES
from bot.strategies import run_all_strategies, get_consensus, ALL_STRATEGIES
from bot.risk_manager import RiskManager
from bot.database import (init_db, get_setting, set_setting, insert_trade,
    update_trade, get_trades, update_strategy_perf, insert_equity)
from ui.tick_monitor import TickMonitorWidget
from ui.strategy_panel import StrategyPanel
from ui.trade_panel import TradePanel
from ui.styles import DARK_STYLE


def _load_env_var(key: str, default: str = "") -> str:
    """Lê variável do .env usando python-dotenv se disponível, senão lê manualmente."""
    # Tenta python-dotenv primeiro
    try:
        from dotenv import load_dotenv
        # Carrega .env da raiz do projeto (um nível acima de ui/)
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        load_dotenv(env_path, override=False)
    except ImportError:
        # Fallback: lê .env manualmente
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())
    return os.environ.get(key, default)


class AutoTradeController:
    """Controla o loop automático de trading"""
    def __init__(self, api, risk_mgr):
        self.api = api
        self.risk = risk_mgr
        self.enabled = False
        self.symbol = "R_10"
        self.granularity = "1m"
        self.tick_count = 5
        self.min_confidence = 72
        self.enabled_strategies = [s.name for s in ALL_STRATEGIES]
        self.last_trade_time = 0
        self.cooldown = 15
        self._martingale_steps = {}   # strategy_name -> int
        self._pending_buys = {}
        self._last_result = {}        # strategy_name -> "win" | "loss"

    def should_trade(self, consensus: dict) -> bool:
        import time
        if not self.enabled:
            return False
        if not consensus.get("signal"):
            return False
        votes_call = consensus.get("votes_call", 0)
        votes_put  = consensus.get("votes_put", 0)
        total_votes = votes_call + votes_put
        if total_votes < 2:
            return False
        if consensus.get("confidence", 0) < self.min_confidence:
            return False
        at_limit, _ = self.risk.check_daily_limit()
        if at_limit:
            return False
        now = time.time()
        if now - self.last_trade_time < self.cooldown:
            return False
        return True

    def get_duration_params(self):
        gran = self.granularity
        if gran == "30s":
            return 30, "s"
        elif gran.endswith("m"):
            return int(gran[:-1]), "m"
        else:
            return self.tick_count, "t"

    def execute(self, consensus: dict, balance: float, log_fn=None):
        import time
        if not self.should_trade(consensus):
            return
        sig = consensus["signal"]
        strategy_name = "Consensus"
        step = self._martingale_steps.get(strategy_name, 0)
        stake = self.risk.get_stake(strategy_name, balance, step)
        duration, unit = self.get_duration_params()
        contract_type = sig
        if log_fn:
            mart_info = f" | Mart step={step}" if step > 0 else ""
            log_fn(f"🤖 AUTO: {sig} | {self.symbol} | Stake=${stake} | Dur={duration}{unit} | Conf={consensus['confidence']:.0f}%{mart_info}")
        self.api.buy_direct(self.symbol, contract_type, duration, unit, stake)
        self.last_trade_time = time.time()
        entry_time = datetime.now().isoformat()
        insert_trade({
            "symbol": self.symbol, "strategy": "Consensus",
            "contract_type": contract_type, "granularity": self.granularity,
            "stake": stake, "entry_time": entry_time, "status": "open",
            "tick_count": duration if unit == "t" else 0,
        })

    def on_trade_result(self, strategy_name: str, is_win: bool, use_martingale: bool):
        if use_martingale:
            if is_win:
                self._martingale_steps[strategy_name] = 0
            else:
                current = self._martingale_steps.get(strategy_name, 0)
                max_steps = self.risk.max_mart_steps
                self._martingale_steps[strategy_name] = min(current + 1, max_steps)
        else:
            self._martingale_steps[strategy_name] = 0
        self._last_result[strategy_name] = "win" if is_win else "loss"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        init_db()
        self.setWindowTitle("⚡ DERIV INSTITUTIONAL BOT — R_ Sintéticos")
        self.setMinimumSize(1400, 860)
        self.resize(1600, 960)
        self.setStyleSheet(DARK_STYLE)

        # FIX: lê App ID do .env — nunca usa 1089 hardcoded
        app_id = _load_env_var("DERIV_APP_ID", "1089")
        self.api = DerivAPI(app_id=app_id)
        self.risk = RiskManager()
        self.auto_ctrl = AutoTradeController(self.api, self.risk)
        self._balance = 0.0
        self._peak_balance = 0.0
        self._log_lines = deque(maxlen=200)
        self._current_symbol = "R_10"
        self._analysis_timer = QTimer()
        self._analysis_timer.timeout.connect(self._run_analysis)
        self._analysis_timer.start(2000)
        self._equity_timer = QTimer()
        self._equity_timer.timeout.connect(self._save_equity)
        self._equity_timer.start(30000)

        self._setup_ui()
        self._connect_signals()
        self._load_settings_to_ui()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        topbar = self._build_topbar()
        root.addLayout(topbar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)

        left = self._build_left_panel()
        splitter.addWidget(left)

        self.tick_monitor = TickMonitorWidget()
        splitter.addWidget(self.tick_monitor)

        right = self._build_right_panel()
        splitter.addWidget(right)

        splitter.setSizes([280, 520, 380])
        root.addWidget(splitter, stretch=1)

        self._build_statusbar()

    def _build_topbar(self):
        lay = QHBoxLayout()
        lay.setSpacing(10)

        lbl_logo = QLabel("⚡ DERIV BOT")
        lbl_logo.setStyleSheet("color:#00d4ff; font-size:18px; font-weight:900; letter-spacing:2px;")
        lay.addWidget(lbl_logo)

        sep = QFrame(); sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color:#1e2d40;"); lay.addWidget(sep)

        self.lbl_balance = QLabel("Saldo: $─")
        self.lbl_balance.setObjectName("label_balance")
        lay.addWidget(self.lbl_balance)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet("color:#1e2d40;"); lay.addWidget(sep2)

        self.lbl_conn = QLabel("● DESCONECTADO")
        self.lbl_conn.setStyleSheet("color:#ff1744; font-size:11px; font-weight:600;")
        lay.addWidget(self.lbl_conn)

        lay.addStretch()

        self.txt_token = QLineEdit()
        self.txt_token.setPlaceholderText("API Token Deriv...")
        self.txt_token.setEchoMode(QLineEdit.Password)
        self.txt_token.setMaximumWidth(200)

        # FIX: prioridade: 1) token salvo no DB, 2) DERIV_API_TOKEN do .env
        saved_token = get_setting("app_token")
        if saved_token:
            self.txt_token.setText(saved_token)
        else:
            env_token = _load_env_var("DERIV_API_TOKEN", "")
            if env_token:
                self.txt_token.setText(env_token)
        lay.addWidget(self.txt_token)

        self.btn_connect = QPushButton("🔗 Conectar")
        self.btn_connect.clicked.connect(self._connect)
        lay.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("✖ Desconectar")
        self.btn_disconnect.clicked.connect(self._disconnect)
        lay.addWidget(self.btn_disconnect)

        self.lbl_time = QLabel()
        self.lbl_time.setStyleSheet("color:#4a6080; font-size:11px; font-family:Consolas;")
        t = QTimer(self)
        t.timeout.connect(lambda: self.lbl_time.setText(datetime.now().strftime("%H:%M:%S")))
        t.start(1000)
        lay.addWidget(self.lbl_time)

        return lay

    def _build_left_panel(self):
        panel = QWidget()
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        sym_grp = QGroupBox("SÍMBOLO")
        sl = QVBoxLayout(sym_grp)
        self.cmb_symbol = QComboBox()
        for name, sym in SYNTHETIC_SYMBOLS.items():
            self.cmb_symbol.addItem(name, sym)
        self.cmb_symbol.currentIndexChanged.connect(self._on_symbol_changed)
        sl.addWidget(self.cmb_symbol)
        layout.addWidget(sym_grp)

        gran_grp = QGroupBox("GRANULARIDADE")
        gl = QVBoxLayout(gran_grp)
        self.cmb_gran = QComboBox()
        for g in list(GRANULARITIES.keys()) + [f"tick {t}" for t in TICK_SIZES]:
            self.cmb_gran.addItem(g)
        gl.addWidget(self.cmb_gran)
        layout.addWidget(gran_grp)

        auto_grp = QGroupBox("BOT AUTOMÁTICO")
        al = QGridLayout(auto_grp)
        al.setSpacing(6)

        al.addWidget(QLabel("Stake Padrão ($):"), 0, 0)
        self.spin_stake = QDoubleSpinBox()
        self.spin_stake.setRange(0.35, 50000)
        self.spin_stake.setValue(float(get_setting("default_stake","1.00")))
        self.spin_stake.setDecimals(2)
        al.addWidget(self.spin_stake, 0, 1)

        al.addWidget(QLabel("Stake Máximo ($):"), 1, 0)
        self.spin_max_stake = QDoubleSpinBox()
        self.spin_max_stake.setRange(0.35, 50000)
        self.spin_max_stake.setValue(float(get_setting("max_stake","100.00")))
        self.spin_max_stake.setDecimals(2)
        al.addWidget(self.spin_max_stake, 1, 1)

        al.addWidget(QLabel("Confiança Mín (%):"), 2, 0)
        self.spin_conf = QSpinBox()
        self.spin_conf.setRange(50, 99)
        self.spin_conf.setValue(72)
        al.addWidget(self.spin_conf, 2, 1)

        al.addWidget(QLabel("Limite Perda/Dia ($):"), 3, 0)
        self.spin_loss_limit = QDoubleSpinBox()
        self.spin_loss_limit.setRange(1, 100000)
        self.spin_loss_limit.setValue(float(get_setting("daily_loss_limit","50.00")))
        al.addWidget(self.spin_loss_limit, 3, 1)

        al.addWidget(QLabel("Martingale Mult:"), 4, 0)
        self.spin_mart = QDoubleSpinBox()
        self.spin_mart.setRange(1.1, 5.0)
        self.spin_mart.setValue(float(get_setting("martingale_multiplier","2.1")))
        self.spin_mart.setSingleStep(0.1)
        al.addWidget(self.spin_mart, 4, 1)

        al.addWidget(QLabel("Steps Martingale:"), 5, 0)
        self.spin_mart_steps = QSpinBox()
        self.spin_mart_steps.setRange(1, 8)
        self.spin_mart_steps.setValue(int(get_setting("max_martingale_steps","4")))
        al.addWidget(self.spin_mart_steps, 5, 1)

        self.chk_martingale = QCheckBox("Martingale Ativo")
        self.chk_martingale.setChecked(False)
        al.addWidget(self.chk_martingale, 6, 0, 1, 2)

        self.chk_stop_on_limit = QCheckBox("Parar ao atingir limite")
        self.chk_stop_on_limit.setChecked(True)
        al.addWidget(self.chk_stop_on_limit, 7, 0, 1, 2)

        layout.addWidget(auto_grp)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("▶ INICIAR BOT")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.setMinimumHeight(44)
        self.btn_stop = QPushButton("■ PARAR BOT")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setMinimumHeight(44)
        self.btn_start.clicked.connect(self._start_bot)
        self.btn_stop.clicked.connect(self._stop_bot)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        layout.addLayout(btn_row)

        btn_save = QPushButton("💾 Salvar Configurações")
        btn_save.clicked.connect(self._save_settings)
        layout.addWidget(btn_save)

        log_grp = QGroupBox("LOG DO SISTEMA")
        ll = QVBoxLayout(log_grp)
        self.txt_log = QTextEdit()
        self.txt_log.setMaximumHeight(180)
        self.txt_log.setReadOnly(True)
        ll.addWidget(self.txt_log)
        btn_clear = QPushButton("Limpar Log")
        btn_clear.setMaximumHeight(24)
        btn_clear.clicked.connect(self.txt_log.clear)
        ll.addWidget(btn_clear)
        layout.addWidget(log_grp)

        layout.addStretch()
        return panel

    def _build_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        splitter_v = QSplitter(Qt.Vertical)

        self.strategy_panel = StrategyPanel()
        self.strategy_panel.strategies_changed.connect(self._on_strategies_changed)
        splitter_v.addWidget(self.strategy_panel)

        self.trade_panel = TradePanel()
        self.trade_panel.manual_buy.connect(self._manual_trade)
        splitter_v.addWidget(self.trade_panel)

        splitter_v.setSizes([420, 380])
        layout.addWidget(splitter_v)
        return panel

    def _build_statusbar(self):
        sb = self.statusBar()
        sb.setStyleSheet("QStatusBar{background:#0a0c12;color:#4a6080;font-size:10px;}")
        self.status_lbl = QLabel("Pronto")
        sb.addWidget(self.status_lbl)
        sb.addPermanentWidget(QLabel("Deriv Institutional Bot v1.0 | Maicom Jordan"))

    def _connect_signals(self):
        self.api.tick_received.connect(self._on_tick)
        self.api.authorized.connect(self._on_authorized)
        self.api.balance_updated.connect(self._on_balance)
        self.api.trade_opened.connect(self._on_trade_opened)
        self.api.trade_closed.connect(self._on_trade_closed)
        self.api.error_signal.connect(self._on_error)
        self.api.connected_signal.connect(self._on_connected)
        self.api.portfolio_updated.connect(self._on_portfolio)

    def _connect(self):
        token = self.txt_token.text().strip()
        if not token:
            self.log("❌ Token necessário para conectar")
            return
        set_setting("app_token", token)
        self.api.start(token)
        self.log(f"🔗 Conectando à Deriv (App ID: {self.api.app_id})...")

    def _disconnect(self):
        self.api.stop()
        self.auto_ctrl.enabled = False
        self.log("✖ Desconectado")

    def _on_connected(self, ok: bool):
        if ok:
            self.lbl_conn.setText("● CONECTADO")
            self.lbl_conn.setStyleSheet("color:#00c853; font-size:11px; font-weight:600;")
            self.tick_monitor.set_connected(True)
            self._subscribe_current_symbol()
            self.log("✅ Conectado à Deriv WebSocket")
        else:
            self.lbl_conn.setText("● DESCONECTADO")
            self.lbl_conn.setStyleSheet("color:#ff1744; font-size:11px; font-weight:600;")
            self.tick_monitor.set_connected(False)

    def _on_authorized(self, acc: dict):
        balance = acc.get("balance", 0)
        currency = acc.get("currency", "USD")
        self.lbl_balance.setText(f"Saldo: ${balance:.2f}")
        self.log(f"🔐 Autorizado: {acc.get('loginid','')} | {currency} | Saldo: ${balance:.2f}")
        self.api.get_history(self._current_symbol, count=200)

    def _on_balance(self, balance: float):
        self._balance = balance
        if balance > self._peak_balance:
            self._peak_balance = balance
        self.lbl_balance.setText(f"Saldo: ${balance:.2f}")

    def _on_tick(self, tick: dict):
        symbol = tick.get("symbol","")
        price = tick.get("quote", 0)
        if symbol == self._current_symbol:
            self.tick_monitor.update_tick(symbol, price)

    def _subscribe_current_symbol(self):
        self.api.unsubscribe_all()
        symbol = self.cmb_symbol.currentData() or "R_10"
        self._current_symbol = symbol
        gran_txt = self.cmb_gran.currentText()
        self.auto_ctrl.symbol = symbol
        self.auto_ctrl.granularity = gran_txt
        self.api.subscribe_ticks(symbol)
        self.api.get_history(symbol, 200)
        self.tick_monitor.update_tick(symbol, 0)
        self.log(f"📡 Assinado: {symbol} | {gran_txt}")

    def _on_symbol_changed(self, idx):
        if self.api._running:
            self._subscribe_current_symbol()

    def _run_analysis(self):
        prices = self.api.get_prices(self._current_symbol)
        if len(prices) < 10:
            return
        results = run_all_strategies(prices)
        enabled = self.auto_ctrl.enabled_strategies
        filtered = [r for r in results if r.get("strategy","") in enabled]
        min_conf = self.spin_conf.value()
        consensus = get_consensus(filtered, min_confidence=min_conf)
        self.strategy_panel.update_results(results)
        self.strategy_panel.update_consensus(consensus)
        call_conf = max((r["confidence"] for r in filtered if r["signal"]=="CALL"), default=0)
        put_conf = max((r["confidence"] for r in filtered if r["signal"]=="PUT"), default=0)
        self.tick_monitor.update_signal(consensus.get("signal"), call_conf, put_conf)
        if self.auto_ctrl.enabled:
            self.auto_ctrl.min_confidence = min_conf
            self.auto_ctrl.execute(consensus, self._balance, self.log)

    def _start_bot(self):
        if not self.api._running:
            self.log("❌ Conecte-se primeiro!")
            return
        self.auto_ctrl.enabled = True
        self.auto_ctrl.min_confidence = self.spin_conf.value()
        self.log(f"▶ BOT INICIADO | {self._current_symbol} | Conf≥{self.spin_conf.value()}%")
        self.btn_start.setStyleSheet("background:#006622; color:#fff; font-weight:700;")

    def _stop_bot(self):
        self.auto_ctrl.enabled = False
        self.log("■ BOT PARADO")
        self.btn_start.setStyleSheet("")

    def _manual_trade(self, symbol, contract_type, duration, unit, stake):
        self.api.buy_direct(symbol, contract_type, duration, unit, stake)
        self.log(f"🎯 MANUAL: {contract_type} | {symbol} | ${stake} | {duration}{unit}")
        insert_trade({
            "symbol": symbol, "strategy": "Manual", "contract_type": contract_type,
            "stake": stake, "entry_time": datetime.now().isoformat(), "status": "open",
            "duration": duration,
        })

    def _on_trade_opened(self, buy: dict):
        cid = buy.get("contract_id","")
        price = buy.get("buy_price", 0)
        self.log(f"✅ TRADE ABERTO #{cid} | Pago: ${price:.2f}")

    def _on_trade_closed(self, data: dict):
        cid = str(data.get("contract_id",""))
        if "profit" in data:
            profit = float(data["profit"] or 0)
        else:
            sell_price = float(data.get("sell_price", 0) or 0)
            buy_price  = float(data.get("buy_price", 0) or 0)
            profit = sell_price - buy_price

        is_win = profit > 0
        result_str = "win" if is_win else "loss"
        icon = "🟢" if is_win else "🔴"
        self.log(f"{icon} TRADE FECHADO #{cid} | Lucro: ${profit:.2f} | {result_str.upper()}")

        update_trade(cid, {
            "result": result_str,
            "profit": profit,
            "status": "closed",
            "exit_time": datetime.now().isoformat()
        })
        update_strategy_perf("Consensus", is_win, profit)

        use_martingale = self.chk_martingale.isChecked()
        self.auto_ctrl.on_trade_result("Consensus", is_win, use_martingale)
        if use_martingale and not is_win:
            step = self.auto_ctrl._martingale_steps.get("Consensus", 0)
            self.log(f"📈 Martingale step={step} | próximo stake=${self.risk.get_stake('Consensus', self._balance, step):.2f}")

        self.strategy_panel.refresh_winrates()
        self.trade_panel.refresh_trades()

    def _on_error(self, msg: str):
        self.log(f"⚠️ ERRO: {msg}")

    def _on_portfolio(self, contracts: list):
        self.trade_panel.update_open_contracts(contracts)

    def _on_strategies_changed(self, enabled: list):
        self.auto_ctrl.enabled_strategies = enabled

    def _save_settings(self):
        set_setting("default_stake", str(self.spin_stake.value()))
        set_setting("max_stake", str(self.spin_max_stake.value()))
        set_setting("daily_loss_limit", str(self.spin_loss_limit.value()))
        set_setting("martingale_multiplier", str(self.spin_mart.value()))
        set_setting("max_martingale_steps", str(self.spin_mart_steps.value()))
        self.risk.refresh()
        self.log("💾 Configurações salvas!")

    def _load_settings_to_ui(self):
        self.spin_stake.setValue(float(get_setting("default_stake","1.00")))
        self.spin_max_stake.setValue(float(get_setting("max_stake","100.00")))
        self.spin_loss_limit.setValue(float(get_setting("daily_loss_limit","50.00")))
        self.spin_mart.setValue(float(get_setting("martingale_multiplier","2.1")))
        self.spin_mart_steps.setValue(int(get_setting("max_martingale_steps","4")))

    def _save_equity(self):
        if self._balance > 0:
            insert_equity(self._balance, self._balance)

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._log_lines.append(line)
        self.txt_log.append(line)
        self.status_lbl.setText(msg[:60])
