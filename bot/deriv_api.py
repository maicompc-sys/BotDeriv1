import asyncio
import json
import websockets
import threading
from datetime import datetime
from collections import deque
from PySide6.QtCore import QObject, Signal

WS_URL = "wss://ws.derivws.com/websockets/v3"

GRANULARITIES = {
    "30s": 30, "1m": 60, "2m": 120, "3m": 180,
    "4m": 240, "5m": 300,
}
TICK_SIZES = list(range(1, 9))

SYNTHETIC_SYMBOLS = {
    "Volatility 10 (R_10)":       "R_10",
    "Volatility 25 (R_25)":       "R_25",
    "Volatility 50 (R_50)":       "R_50",
    "Volatility 75 (R_75)":       "R_75",
    "Volatility 100 (R_100)":     "R_100",
    "Volatility 10 1s (1HZ10V)":  "1HZ10V",
    "Volatility 25 1s (1HZ25V)":  "1HZ25V",
    "Volatility 50 1s (1HZ50V)":  "1HZ50V",
    "Volatility 75 1s (1HZ75V)":  "1HZ75V",
    "Volatility 100 1s (1HZ100V)":"1HZ100V",
}

def _is_open(ws):
    """Compatível com websockets v10, v11, v12+"""
    if ws is None:
        return False
    if hasattr(ws, 'open'):
        return ws.open
    if hasattr(ws, 'state'):
        try:
            import websockets.connection as wsc
            return ws.state == wsc.State.OPEN
        except Exception:
            pass
        try:
            return str(ws.state).upper() == "OPEN"
        except Exception:
            pass
    if hasattr(ws, 'closed'):
        return not ws.closed
    return False


class DerivAPI(QObject):
    tick_received     = Signal(dict)
    candle_received   = Signal(dict)
    authorized        = Signal(dict)
    balance_updated   = Signal(float)
    trade_opened      = Signal(dict)
    trade_closed      = Signal(dict)
    error_signal      = Signal(str)
    connected_signal  = Signal(bool)
    portfolio_updated = Signal(list)
    proposal_received = Signal(dict)  # novo: recebe proposta antes do buy

    def __init__(self, app_id="1089"):
        super().__init__()
        self.app_id       = app_id
        self._ws          = None
        self._loop        = None
        self._thread      = None
        self._running     = False
        self._token       = ""
        self._req_id      = 0  # FIX: começa em 0 para retornar 1 na primeira chamada
        self._prices      = {}
        self.balance      = 0.0
        self.currency     = "USD"
        self.account_info = {}
        # FIX: armazena propostas pendentes aguardando buy {req_id: params}
        self._pending_proposals = {}

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    def start(self, token: str):
        self._token = token
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread  = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect())

    async def _connect(self):
        url = f"{WS_URL}?app_id={self.app_id}"
        while self._running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self.connected_signal.emit(True)
                    await asyncio.sleep(0.3)
                    if self._token:
                        await self._send_raw({
                            "authorize": self._token,
                            "req_id": self._next_id()
                        })
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            await self._handle(json.loads(message))
                        except Exception:
                            pass
            except Exception as e:
                self._ws = None
                self.connected_signal.emit(False)
                if self._running:
                    self.error_signal.emit(f"Reconectando... ({type(e).__name__})")
                    await asyncio.sleep(3)
        self._ws = None
        self.connected_signal.emit(False)

    async def _disconnect(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _send_raw(self, payload: dict):
        if self._ws and _is_open(self._ws):
            await self._ws.send(json.dumps(payload))

    def send(self, payload: dict):
        if self._loop and self._running:
            asyncio.run_coroutine_threadsafe(self._send_raw(payload), self._loop)

    async def _handle(self, data: dict):
        if data.get("error"):
            code = data["error"].get("code", "")
            msg = data["error"].get("message", str(data))
            if code not in ("AlreadySubscribed",):
                self.error_signal.emit(msg)
            # FIX: limpa proposta pendente em caso de erro
            req_id = data.get("req_id")
            if req_id and req_id in self._pending_proposals:
                del self._pending_proposals[req_id]
            return

        msg_type = data.get("msg_type")

        if msg_type == "authorize":
            acc            = data.get("authorize", {})
            self.balance   = acc.get("balance", 0)
            self.currency  = acc.get("currency", "USD")
            self.account_info = acc
            self.authorized.emit(acc)
            self.balance_updated.emit(self.balance)
            # FIX: subscribe_balance apenas aqui, não duplicar em _on_authorized
            await self._send_raw({"balance": 1, "subscribe": 1, "req_id": self._next_id()})

        elif msg_type == "balance":
            bal          = data.get("balance", {})
            self.balance = bal.get("balance", self.balance)
            self.balance_updated.emit(self.balance)

        elif msg_type == "tick":
            tick   = data.get("tick", {})
            symbol = tick.get("symbol", "")
            price  = tick.get("quote", 0)
            if symbol not in self._prices:
                self._prices[symbol] = deque(maxlen=500)
            self._prices[symbol].append(price)
            tick["prices"] = list(self._prices[symbol])
            self.tick_received.emit(tick)

        elif msg_type == "ohlc":
            self.candle_received.emit(data.get("ohlc", {}))

        elif msg_type == "history":
            hist   = data.get("history", {})
            symbol = data.get("echo_req", {}).get("ticks_history", "")
            prices = hist.get("prices", [])
            if symbol and prices:
                if symbol not in self._prices:
                    self._prices[symbol] = deque(maxlen=500)
                for p in prices:
                    self._prices[symbol].append(float(p))

        elif msg_type == "proposal":
            # FIX: ao receber proposta, executa o buy com o id correto
            proposal = data.get("proposal", {})
            req_id   = data.get("req_id")
            if req_id and req_id in self._pending_proposals:
                pending = self._pending_proposals.pop(req_id)
                await self._send_raw({
                    "buy":    proposal.get("id"),
                    "price":  pending["stake"],
                    "req_id": self._next_id()
                })
            self.proposal_received.emit(proposal)

        elif msg_type == "buy":
            self.trade_opened.emit(data.get("buy", {}))

        elif msg_type == "sell":
            self.trade_closed.emit(data.get("sell", {}))

        elif msg_type == "proposal_open_contract":
            poc = data.get("proposal_open_contract", {})
            if poc.get("is_sold"):
                self.trade_closed.emit(poc)

        elif msg_type == "portfolio":
            self.portfolio_updated.emit(
                data.get("portfolio", {}).get("contracts", []))

    # ── Public API ────────────────────────────────────────────────────────
    def subscribe_ticks(self, symbol: str):
        self.send({"ticks": symbol, "subscribe": 1, "req_id": self._next_id()})

    def unsubscribe_all(self):
        self.send({"forget_all": "ticks",   "req_id": self._next_id()})
        self.send({"forget_all": "candles", "req_id": self._next_id()})

    def subscribe_balance(self):
        # FIX: mantido para uso externo mas o subscribe inicial é feito
        # automaticamente após authorize para evitar duplicação
        self.send({"balance": 1, "subscribe": 1, "req_id": self._next_id()})

    def get_history(self, symbol: str, count: int = 200):
        self.send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "style": "ticks",
            "req_id": self._next_id()
        })

    def buy_direct(self, symbol: str, contract_type: str,
                   duration: int, duration_unit: str,
                   stake: float, barrier: str = None):
        """
        FIX: fluxo correto Deriv API — envia 'proposal' primeiro,
        ao receber a resposta executa 'buy' com o proposal id.
        """
        req_id = self._next_id()
        params = {
            "amount":        stake,
            "basis":         "stake",
            "contract_type": contract_type,
            "currency":      self.currency or "USD",
            "duration":      duration,
            "duration_unit": duration_unit,
            "symbol":        symbol,
        }
        if barrier is not None:
            params["barrier"] = barrier
        # armazena para uso quando a proposta retornar
        self._pending_proposals[req_id] = {"stake": stake, "params": params}
        self.send({
            "proposal":   1,
            "req_id":     req_id,
            **params
        })

    def get_portfolio(self):
        self.send({"portfolio": 1, "req_id": self._next_id()})

    def get_prices(self, symbol: str) -> list:
        return list(self._prices.get(symbol, []))
