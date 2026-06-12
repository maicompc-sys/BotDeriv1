"""
deriv_api.py — Deriv API Client com autenticação automática PAT
Suporta tokens alfanuméricos (nova API developers.deriv.com).
Suporta troca entre conta DEMO e REAL sem digitar nada.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

import websockets
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ─── Credenciais do .env ──────────────────────────────────────────────────────
DERIV_APP_ID       = os.getenv("DERIV_APP_ID", "")
DERIV_TOKEN_REAL   = os.getenv("DERIV_API_TOKEN_REAL", "")
DERIV_TOKEN_DEMO   = os.getenv("DERIV_API_TOKEN_DEMO", "")
DERIV_WS_URL       = os.getenv("DERIV_WS_URL", "wss://ws.derivws.com/websockets/v3")

WS_ENDPOINT = f"{DERIV_WS_URL}?app_id={DERIV_APP_ID}"

ACCOUNT_MODES = {
    "demo": DERIV_TOKEN_DEMO,
    "real": DERIV_TOKEN_REAL,
}

# Padrão dos novos tokens alfanuméricos da Deriv (ex: pat_xxx... ou token de 64 chars hex)
_PAT_PATTERN   = re.compile(r"^pat_[a-f0-9]{64}$")
_LEGACY_PATTERN = re.compile(r"^[A-Za-z0-9]{15,32}$")


def validate_token(token: str) -> str:
    """
    Valida e identifica o formato do token Deriv.
    Retorna: 'pat_alphanumeric' | 'legacy' | 'unknown'
    """
    if not token:
        return "empty"
    if _PAT_PATTERN.match(token):
        return "pat_alphanumeric"
    if _LEGACY_PATTERN.match(token):
        return "legacy"
    return "unknown"


def validate_app_id(app_id: str) -> bool:
    """
    Valida o App ID alfanumérico da nova API Deriv (developers.deriv.com).
    Formato novo: string alfanumérica (ex: 33wQj4vvambGV9iRyHOTh)
    Formato legado: string numérica (ex: 1089)
    """
    if not app_id:
        return False
    # Aceita tanto numérico puro (legado) quanto alfanumérico (novo)
    return bool(re.match(r"^[A-Za-z0-9_-]{4,30}$", app_id))


# ─── Cliente principal ────────────────────────────────────────────────────────

class DerivAPIClient:
    """
    Cliente assíncrono para a Deriv API v3.
    - Autenticação 100% automática via PAT (sem input manual)
    - Suporte a tokens alfanuméricos (nova API developers.deriv.com)
    - Troca entre conta DEMO e REAL em tempo real (switch_account)
    - Reconexão automática com backoff exponencial
    """

    def __init__(self, mode: str = "demo"):
        """
        mode: "demo" | "real"
        Pode ser trocado depois com await client.switch_account("real")
        """
        self.mode = mode.lower()
        self._validate_mode(self.mode)

        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.req_id   = 1
        self.pending:  Dict[int, asyncio.Future] = {}
        self._subs:    Dict[str, Callable]        = {}
        self._listener_task: Optional[asyncio.Task] = None

        self.is_authorized  = False
        self.account_info:  Dict = {}
        self.all_accounts:  List[Dict] = []
        self.token_format:  str = "unknown"

    # ─── Validação ────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_mode(mode: str):
        if mode not in ACCOUNT_MODES:
            raise ValueError(f"mode deve ser 'demo' ou 'real', recebido: '{mode}'")

    def _get_token(self) -> str:
        token = ACCOUNT_MODES[self.mode]
        if not token:
            raise EnvironmentError(
                f"Token para modo '{self.mode}' não encontrado no .env.\n"
                f"Defina DERIV_API_TOKEN_{'REAL' if self.mode == 'real' else 'DEMO'}\n"
                f"Gere seu token em: https://developers.deriv.com"
            )
        fmt = validate_token(token)
        self.token_format = fmt
        if fmt == "empty":
            raise EnvironmentError("Token vazio no .env")
        if fmt == "unknown":
            logger.warning(
                f"[{self.mode.upper()}] Formato de token não reconhecido. "
                f"Esperado: pat_<64-hex> (novo) ou alfanumérico 15-32 chars (legado). "
                f"Token: {token[:12]}..."
            )
        else:
            logger.info(f"[{self.mode.upper()}] Formato de token detectado: {fmt}")
        return token

    # ─── Conexão ─────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Conecta e autentica automaticamente. Sem input do usuário."""
        if not DERIV_APP_ID:
            raise EnvironmentError(
                "DERIV_APP_ID não encontrado no .env\n"
                "Gere seu App ID em: https://developers.deriv.com"
            )

        if not validate_app_id(DERIV_APP_ID):
            raise EnvironmentError(
                f"DERIV_APP_ID inválido: '{DERIV_APP_ID}'\n"
                f"O App ID deve ser alfanumérico (4-30 chars).\n"
                f"Gere um novo em: https://developers.deriv.com"
            )

        logger.info(f"[{self.mode.upper()}] Conectando em {WS_ENDPOINT}")
        try:
            self.ws = await websockets.connect(
                WS_ENDPOINT,
                ping_interval=25,
                ping_timeout=10,
                close_timeout=5,
            )
            self._listener_task = asyncio.create_task(self._listener())
            await self._authorize()
            return True
        except Exception as e:
            logger.error(f"Falha na conexão: {e}")
            return False

    async def disconnect(self):
        if self._listener_task:
            self._listener_task.cancel()
        if self.ws and self.ws.open:
            await self.ws.close()
        self.is_authorized = False
        logger.info("Desconectado.")

    # ─── Autenticação automática ──────────────────────────────────────────────

    async def _authorize(self) -> Dict:
        """
        Envia o PAT correto para o modo atual e armazena info das contas.
        Suporta tokens alfanuméricos (pat_xxx) e legados.
        Totalmente automático — sem nenhum input do usuário.
        """
        token = self._get_token()
        logger.info(
            f"[{self.mode.upper()}] Autenticando com PAT "
            f"(formato: {self.token_format})..."
        )

        response = await self._send({"authorize": token})

        if response.get("error"):
            err = response["error"]
            code = err.get('code', '')
            msg  = err.get('message', '')
            hint = ""
            if code in ("InvalidToken", "InvalidAppID"):
                hint = (
                    "\n→ Verifique suas credenciais em https://developers.deriv.com"
                    "\n→ O token deve ter escopos: read, trade, trading_information"
                )
            raise PermissionError(
                f"[{self.mode.upper()}] Autenticação falhou: [{code}] {msg}{hint}"
            )

        auth = response.get("authorize", {})
        self.is_authorized = True
        self.account_info  = auth
        self.all_accounts  = auth.get("account_list", [])

        logger.info(
            f"[{self.mode.upper()}] ✓ Autenticado | "
            f"Conta: {auth.get('loginid')} | "
            f"Saldo: {auth.get('balance')} {auth.get('currency')} | "
            f"Tipo: {'Virtual/Demo' if auth.get('is_virtual') else 'Real'} | "
            f"Token: {self.token_format}"
        )
        return auth

    # ─── Troca de conta DEMO ↔ REAL ──────────────────────────────────────────

    async def switch_account(self, mode: str) -> Dict:
        """
        Troca entre conta DEMO e REAL sem reconectar o WebSocket.
        Exemplo: await client.switch_account("real")
        """
        mode = mode.lower()
        self._validate_mode(mode)

        if mode == self.mode:
            logger.info(f"Já está no modo '{mode}'.")
            return self.account_info

        logger.info(f"Trocando conta: {self.mode.upper()} → {mode.upper()}")
        self.mode = mode
        self.is_authorized = False
        auth = await self._authorize()
        return auth

    def get_current_mode(self) -> str:
        return self.mode

    def get_balance(self) -> float:
        return float(self.account_info.get("balance", 0))

    def get_currency(self) -> str:
        return self.account_info.get("currency", "USD")

    def get_loginid(self) -> str:
        return self.account_info.get("loginid", "")

    def is_demo(self) -> bool:
        return bool(self.account_info.get("is_virtual", False))

    def get_token_info(self) -> Dict:
        """Retorna informações sobre o token em uso (sem expor o valor)."""
        token = ACCOUNT_MODES.get(self.mode, "")
        return {
            "mode": self.mode,
            "format": self.token_format,
            "prefix": token[:8] + "..." if token else "(vazio)",
            "authorized": self.is_authorized,
        }

    # ─── Ping ─────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            res = await self._send({"ping": 1})
            return res.get("ping") == "pong"
        except Exception:
            return False

    # ─── Mercado ──────────────────────────────────────────────────────────────

    async def get_active_symbols(self, market_type: str = "synthetic_index") -> list:
        res = await self._send({
            "active_symbols": "brief",
            "product_type": market_type,
        })
        if res.get("error"):
            logger.warning(f"active_symbols: {res['error']}")
            return []
        return res.get("active_symbols", [])

    async def get_candles(
        self,
        symbol: str,
        granularity: int = 60,
        count: int = 200,
    ) -> list:
        """OHLCV candles. granularity em segundos (60, 300, 900, 3600, 86400)."""
        res = await self._send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "granularity": granularity,
            "style": "candles",
        })
        if res.get("error"):
            logger.error(f"get_candles: {res['error']}")
            return []
        return res.get("candles", [])

    async def get_ticks_history(self, symbol: str, count: int = 500) -> list:
        """Últimos N ticks de um símbolo."""
        res = await self._send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "style": "ticks",
        })
        if res.get("error"):
            return []
        return res.get("history", {}).get("prices", [])

    async def subscribe_ticks(
        self, symbol: str, callback: Callable[[Dict], Any]
    ) -> Optional[str]:
        """Assina tick stream em tempo real."""
        payload = {"ticks": symbol, "subscribe": 1}
        req_id  = self.req_id
        self.req_id += 1
        payload["req_id"] = req_id

        self._subs[f"ticks_{symbol}"] = callback

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[req_id] = future

        await self.ws.send(json.dumps(payload))
        first = await asyncio.wait_for(future, timeout=15)

        sub_id = first.get("subscription", {}).get("id")
        logger.info(f"Subscrito em ticks {symbol} | sub_id={sub_id}")
        return sub_id

    async def unsubscribe(self, subscription_id: str):
        await self._send({"forget": subscription_id})

    async def unsubscribe_all(self):
        await self._send({"forget_all": "ticks"})
        self._subs.clear()

    # ─── Proposta & Compra ────────────────────────────────────────────────────

    async def get_price_proposal(
        self,
        symbol: str,
        contract_type: str,
        duration: int,
        duration_unit: str = "t",
        stake: float = 1.0,
        basis: str = "stake",
        currency: Optional[str] = None,
    ) -> Dict:
        """
        Solicita proposta de preço.
        contract_type: CALL | PUT | DIGITEVEN | DIGITODD | DIGITMATCH | DIGITDIFF
        duration_unit: t (ticks) | s | m | h | d
        """
        currency = currency or self.get_currency()
        res = await self._send({
            "proposal": 1,
            "amount": stake,
            "basis": basis,
            "contract_type": contract_type,
            "currency": currency,
            "duration": duration,
            "duration_unit": duration_unit,
            "symbol": symbol,
        })
        if res.get("error"):
            logger.error(f"Proposta recusada [{contract_type}]: {res['error']}")
        return res

    async def buy_contract(self, proposal_id: str, price: float) -> Dict:
        """Compra o contrato. price = valor máximo aceito (proteção slippage)."""
        if not self.is_authorized:
            raise PermissionError("Não autenticado.")

        res = await self._send({"buy": proposal_id, "price": price})
        if res.get("error"):
            logger.error(f"buy_contract: {res['error']}")
        else:
            b = res.get("buy", {})
            logger.info(
                f"[{self.mode.upper()}] Contrato aberto | "
                f"id={b.get('contract_id')} | "
                f"stake={b.get('buy_price')} | payout={b.get('payout')}"
            )
        return res

    async def sell_contract(self, contract_id: int, price: float = 0) -> Dict:
        """Vende/fecha um contrato antecipadamente."""
        res = await self._send({"sell": contract_id, "price": price})
        if res.get("error"):
            logger.error(f"sell_contract: {res['error']}")
        return res

    # ─── Conta ────────────────────────────────────────────────────────────────

    async def get_account_balance(self) -> Dict:
        res = await self._send({"balance": 1, "account": "current"})
        if res.get("error"):
            return {}
        return res.get("balance", {})

    async def get_open_contracts(self) -> list:
        res = await self._send({"portfolio": 1})
        if res.get("error"):
            return []
        return res.get("portfolio", {}).get("contracts", [])

    async def get_profit_table(self, limit: int = 50) -> list:
        res = await self._send({
            "profit_table": 1,
            "description": 1,
            "limit": limit,
            "sort": "DESC",
        })
        if res.get("error"):
            return []
        return res.get("profit_table", {}).get("transactions", [])

    async def get_statement(self, limit: int = 50) -> list:
        res = await self._send({
            "statement": 1,
            "description": 1,
            "limit": limit,
        })
        if res.get("error"):
            return []
        return res.get("statement", {}).get("transactions", [])

    # ─── Infraestrutura ───────────────────────────────────────────────────────

    async def _send(self, payload: Dict[str, Any], timeout: float = 15.0) -> Dict:
        if self.ws is None or not self.ws.open:
            raise ConnectionError("WebSocket não conectado.")

        req_id = self.req_id
        self.req_id += 1
        payload["req_id"] = req_id

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[req_id] = future

        await self.ws.send(json.dumps(payload))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self.pending.pop(req_id, None)
            raise TimeoutError(f"Timeout req_id={req_id}")

    async def _listener(self):
        try:
            async for raw in self.ws:
                try:
                    msg: Dict = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                rid = msg.get("req_id")
                if rid and rid in self.pending:
                    fut = self.pending.pop(rid)
                    if not fut.done():
                        fut.set_result(msg)

                mtype = msg.get("msg_type")
                if mtype == "tick":
                    sym = msg.get("tick", {}).get("symbol", "")
                    cb  = self._subs.get(f"ticks_{sym}")
                    if cb:
                        if asyncio.iscoroutinefunction(cb):
                            asyncio.create_task(cb(msg))
                        else:
                            cb(msg)

                err_code = msg.get("error", {}).get("code", "")
                if err_code in ("InvalidToken", "AuthorizationRequired"):
                    logger.critical(
                        "Token inválido/expirado! "
                        "Gere um novo em https://developers.deriv.com"
                    )
                    self.is_authorized = False

        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"Conexão fechada: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Listener erro: {e}")


# ─── Reconexão automática ─────────────────────────────────────────────────────

class DerivClient(DerivAPIClient):
    """
    Versão de produção com reconexão automática + backoff exponencial.
    Use esta classe no main.py.

    Exemplo rápido:
        client = DerivClient(mode="demo")   # inicia em demo
        await client.connect()              # autentica automaticamente

        await client.switch_account("real") # troca para real sem reconectar
        await client.switch_account("demo") # volta para demo
    """

    def __init__(self, mode: str = "demo", max_retries: int = 10, base_delay: float = 2.0):
        super().__init__(mode=mode)
        self.max_retries = max_retries
        self.base_delay  = base_delay

    async def connect(self) -> bool:
        for attempt in range(1, self.max_retries + 1):
            try:
                ok = await super().connect()
                if ok:
                    return True
            except PermissionError as e:
                # Erro de credencial — não adianta reconectar
                logger.error(f"Erro de autenticação (não tentará reconectar): {e}")
                return False
            except Exception as e:
                delay = min(self.base_delay * (2 ** (attempt - 1)), 120)
                logger.warning(
                    f"Tentativa {attempt}/{self.max_retries} falhou ({e}). "
                    f"Aguardando {delay:.0f}s..."
                )
                await asyncio.sleep(delay)

        logger.error("Conexão esgotada após todas as tentativas.")
        return False

    async def ensure_connected(self):
        """Chame periodicamente para manter a conexão viva."""
        if not await self.ping():
            logger.info("Reconectando...")
            await self.connect()
