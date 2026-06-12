"""
deriv_api.py — Deriv WebSocket API Client v3

Autenticação automática via PAT (Personal Access Token).
Suporta tokens alfanuméricos (novo formato: pat_<64hex>).
Suporta App ID alfanumérico (ex: 33wQj4vvambGV9iRyHOTh).
Troca DEMO ↔ REAL sem reconectar o WebSocket.
Reconexão automática com backoff exponencial.
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

# ─── Credenciais ──────────────────────────────────────────────────────────────
DERIV_APP_ID     = os.getenv("DERIV_APP_ID", "")
DERIV_TOKEN_REAL = os.getenv("DERIV_API_TOKEN_REAL", "")
DERIV_TOKEN_DEMO = os.getenv("DERIV_API_TOKEN_DEMO", "")
DERIV_WS_URL     = os.getenv("DERIV_WS_URL", "wss://ws.derivws.com/websockets/v3")

# App ID vai como query string na URL (obrigatório na v3)
WS_ENDPOINT = f"{DERIV_WS_URL}?app_id={DERIV_APP_ID}"

ACCOUNT_MODES: Dict[str, str] = {
    "demo": DERIV_TOKEN_DEMO,
    "real": DERIV_TOKEN_REAL,
}

# Regex para validar formatos de token
_PAT_PATTERN    = re.compile(r"^pat_[a-f0-9]{64}$")          # novo: pat_<64hex>
_LEGACY_PATTERN = re.compile(r"^[A-Za-z0-9]{15,32}$")        # legado: alfanum 15-32
# App ID: alfanumérico 4-30 chars (ex: 33wQj4vvambGV9iRyHOTh ou numérico legado)
_APPID_PATTERN  = re.compile(r"^[A-Za-z0-9_-]{4,30}$")


def validate_token(token: str) -> str:
    """
    Identifica o formato do token.
    Retorna: 'pat_alphanumeric' | 'legacy' | 'unknown' | 'empty'
    """
    if not token:
        return "empty"
    if _PAT_PATTERN.match(token):
        return "pat_alphanumeric"
    if _LEGACY_PATTERN.match(token):
        return "legacy"
    return "unknown"


def validate_app_id(app_id: str) -> bool:
    """Valida App ID alfanumérico (novo) ou numérico (legado)."""
    return bool(app_id and _APPID_PATTERN.match(app_id))


# ─── Cliente principal ────────────────────────────────────────────────────────

class DerivAPIClient:
    """
    Cliente assíncrono para a Deriv WebSocket API v3.

    Uso básico:
        client = DerivAPIClient(mode="demo")
        await client.connect()
        balance = await client.get_account_balance()
        await client.disconnect()

    Troca de conta:
        await client.switch_account("real")
    """

    def __init__(self, mode: str = "demo"):
        self.mode = mode.lower()
        self._validate_mode(self.mode)

        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._req_id: int = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._subscriptions: Dict[str, Callable] = {}
        self._listener_task: Optional[asyncio.Task] = None

        self.is_authorized = False
        self.account_info: Dict = {}
        self.all_accounts: List[Dict] = []
        self.token_format: str = "unknown"

    # ─── Validação ────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_mode(mode: str) -> None:
        if mode not in ACCOUNT_MODES:
            raise ValueError(f"mode deve ser 'demo' ou 'real', recebido: '{mode}'")

    def _get_token(self) -> str:
        token = ACCOUNT_MODES[self.mode]
        if not token:
            key = "DERIV_API_TOKEN_REAL" if self.mode == "real" else "DERIV_API_TOKEN_DEMO"
            raise EnvironmentError(
                f"Token para modo '{self.mode}' não encontrado no .env.\n"
                f"Defina {key}=pat_<seu_token>\n"
                f"Gere seu PAT em: https://app.deriv.com/account/api-token"
            )
        fmt = validate_token(token)
        self.token_format = fmt
        if fmt == "unknown":
            logger.warning(
                f"[{self.mode.upper()}] Formato de token desconhecido. "
                f"Esperado: pat_<64hex>. Token: {token[:12]}..."
            )
        else:
            logger.info(f"[{self.mode.upper()}] Token detectado: {fmt}")
        return token

    # ─── Conexão / Desconexão ─────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Conecta ao WebSocket e autentica automaticamente."""
        if not DERIV_APP_ID:
            raise EnvironmentError(
                "DERIV_APP_ID não configurado no .env\n"
                "Gere em: https://developers.deriv.com"
            )
        if not validate_app_id(DERIV_APP_ID):
            raise EnvironmentError(
                f"DERIV_APP_ID inválido: '{DERIV_APP_ID}'\n"
                "Formato esperado: alfanumérico 4-30 chars."
            )

        logger.info(f"[{self.mode.upper()}] Conectando → {WS_ENDPOINT}")
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
        except Exception as exc:
            logger.error(f"Falha na conexão: {exc}")
            return False

    async def disconnect(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
        if self.ws and self.ws.open:
            await self.ws.close()
        self.is_authorized = False
        logger.info("Desconectado.")

    # ─── Loop de leitura ──────────────────────────────────────────────────────

    async def _listener(self) -> None:
        """Recebe mensagens e resolve Futures pendentes ou dispara callbacks."""
        try:
            async for raw in self.ws:
                msg: Dict = json.loads(raw)
                req_id = msg.get("req_id")

                # Resposta a uma requisição com req_id
                if req_id and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)

                # Dados de subscrição contínua (stream)
                elif msg.get("subscription"):
                    sub_id = msg["subscription"].get("id")
                    cb = self._subscriptions.get(sub_id)
                    if cb:
                        try:
                            asyncio.create_task(cb(msg))
                        except Exception as e:
                            logger.error(f"Erro no callback de subscrição: {e}")

        except websockets.ConnectionClosed:
            logger.warning("Conexão WebSocket encerrada.")
        except Exception as exc:
            logger.error(f"Erro no listener: {exc}")

    # ─── Envio de requisições ─────────────────────────────────────────────────

    async def _send(self, payload: Dict, timeout: float = 30.0) -> Dict:
        """
        Envia uma requisição JSON e aguarda a resposta via req_id.
        Thread-safe: cada requisição tem seu próprio Future.
        """
        if not self.ws or not self.ws.open:
            raise ConnectionError("WebSocket não conectado.")

        self._req_id += 1
        rid = self._req_id
        payload["req_id"] = rid

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[rid] = fut

        await self.ws.send(json.dumps(payload))

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise TimeoutError(f"Timeout ({timeout}s) na requisição: {payload}")

    # ─── Autenticação ─────────────────────────────────────────────────────────

    async def _authorize(self) -> Dict:
        """
        Envia o PAT para autenticar a sessão WebSocket.
        O token vai no payload — o App ID já está na URL de conexão.
        """
        token = self._get_token()
        logger.info(f"[{self.mode.upper()}] Autenticando (formato: {self.token_format})...")

        response = await self._send({"authorize": token})

        if response.get("error"):
            err  = response["error"]
            code = err.get("code", "")
            msg  = err.get("message", "")
            hint = ""
            if code in ("InvalidToken", "InvalidAppID", "AuthorizationRequired"):
                hint = (
                    "\n→ Verifique: https://developers.deriv.com"
                    "\n→ Escopos necessários: read, trade, trading_information"
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
            f"loginid={auth.get('loginid')} | "
            f"saldo={auth.get('balance')} {auth.get('currency')} | "
            f"tipo={'Virtual/Demo' if auth.get('is_virtual') else 'Real'}"
        )
        return auth

    # ─── Troca de conta ───────────────────────────────────────────────────────

    async def switch_account(self, mode: str) -> Dict:
        """Troca entre DEMO e REAL sem reconectar o WebSocket."""
        mode = mode.lower()
        self._validate_mode(mode)
        if mode == self.mode:
            logger.info(f"Já no modo '{mode}'.")
            return self.account_info
        logger.info(f"Trocando: {self.mode.upper()} → {mode.upper()}")
        self.mode = mode
        self.is_authorized = False
        return await self._authorize()

    # ─── Getters ──────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        return float(self.account_info.get("balance", 0.0))

    def get_currency(self) -> str:
        return self.account_info.get("currency", "USD")

    def get_loginid(self) -> str:
        return self.account_info.get("loginid", "")

    def is_demo(self) -> bool:
        return bool(self.account_info.get("is_virtual", False))

    def get_current_mode(self) -> str:
        return self.mode

    def get_token_info(self) -> Dict:
        """Informações do token em uso (sem expor o valor)."""
        raw = ACCOUNT_MODES.get(self.mode, "")
        return {
            "mode": self.mode,
            "format": self.token_format,
            "prefix": (raw[:8] + "...") if raw else "(vazio)",
            "authorized": self.is_authorized,
        }

    # ─── Ping ─────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            res = await self._send({"ping": 1})
            return res.get("ping") == "pong"
        except Exception:
            return False

    # ─── Saldo em tempo real ──────────────────────────────────────────────────

    async def get_account_balance(self, subscribe: bool = False) -> Dict:
        """Retorna saldo atual. subscribe=True inicia stream de atualizações."""
        payload: Dict = {"balance": 1, "account": "current"}
        if subscribe:
            payload["subscribe"] = 1
        return await self._send(payload)

    # ─── Mercado ──────────────────────────────────────────────────────────────

    async def get_active_symbols(self, market_type: str = "synthetic_index") -> List[Dict]:
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
    ) -> List[Dict]:
        """OHLCV. granularity em segundos: 60, 300, 900, 3600, 86400."""
        res = await self._send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "granularity": granularity,
            "style": "candles",
        })
        if res.get("error"):
            logger.warning(f"get_candles({symbol}): {res['error']}")
            return []
        return res.get("candles", [])

    async def get_ticks(
        self,
        symbol: str,
        count: int = 100,
        subscribe: bool = False,
        callback: Optional[Callable] = None,
    ) -> Dict:
        """Histórico de ticks. subscribe=True inicia stream."""
        payload: Dict = {
            "ticks_history": symbol,
            "count": count,
            "end": "latest",
            "style": "ticks",
        }
        if subscribe:
            payload["subscribe"] = 1
        res = await self._send(payload)
        if subscribe and callback and res.get("subscription"):
            sub_id = res["subscription"]["id"]
            self._subscriptions[sub_id] = callback
        return res

    # ─── Contratos ────────────────────────────────────────────────────────────

    async def get_price_proposal(
        self,
        symbol: str,
        contract_type: str,
        duration: int,
        duration_unit: str,
        stake: float,
        currency: str = "USD",
        basis: str = "stake",
    ) -> Dict:
        """
        Solicita proposta de preço para um contrato.

        contract_type: CALL | PUT | DIGITEVEN | DIGITODD | ...
        duration_unit: t (ticks) | s (segundos) | m | h | d
        """
        return await self._send({
            "proposal": 1,
            "amount": stake,
            "basis": basis,
            "contract_type": contract_type,
            "currency": currency,
            "duration": duration,
            "duration_unit": duration_unit,
            "symbol": symbol,
        })

    async def buy_contract(
        self,
        proposal_id: str,
        price: float,
    ) -> Dict:
        """Compra um contrato usando o proposal_id retornado por get_price_proposal."""
        return await self._send({
            "buy": proposal_id,
            "price": price,
        })

    async def sell_contract(
        self,
        contract_id: int,
        price: float = 0,
    ) -> Dict:
        """Vende um contrato aberto. price=0 vende a mercado."""
        return await self._send({
            "sell": contract_id,
            "price": price,
        })

    async def get_open_contracts(self) -> List[Dict]:
        """Retorna contratos abertos da conta."""
        res = await self._send({"portfolio": 1})
        if res.get("error"):
            logger.warning(f"portfolio: {res['error']}")
            return []
        return res.get("portfolio", {}).get("contracts", [])

    async def get_contract_details(self, contract_id: int) -> Dict:
        """Detalhes de um contrato específico."""
        return await self._send({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
        })

    async def get_profit_table(
        self,
        limit: int = 50,
        offset: int = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict:
        """Histórico de trades (profit table)."""
        payload: Dict = {
            "profit_table": 1,
            "description": 1,
            "limit": limit,
            "offset": offset,
            "sort": "DESC",
        }
        if date_from:
            payload["date_from"] = date_from
        if date_to:
            payload["date_to"] = date_to
        return await self._send(payload)

    # ─── Subscrições ──────────────────────────────────────────────────────────

    async def subscribe_ticks(
        self,
        symbol: str,
        callback: Callable[[Dict], Any],
    ) -> str:
        """
        Inicia stream de ticks para um símbolo.
        Retorna sub_id para cancelar com unsubscribe().
        """
        res = await self._send({"ticks": symbol, "subscribe": 1})
        if res.get("error"):
            raise RuntimeError(f"subscribe_ticks({symbol}): {res['error']}")
        sub_id = res["subscription"]["id"]
        self._subscriptions[sub_id] = callback
        logger.info(f"Subscrito em ticks: {symbol} (id={sub_id})")
        return sub_id

    async def unsubscribe(self, sub_id: str) -> Dict:
        """Cancela uma subscrição pelo ID."""
        self._subscriptions.pop(sub_id, None)
        return await self._send({"forget": sub_id})

    async def unsubscribe_all(self) -> Dict:
        """Cancela todas as subscrições ativas."""
        self._subscriptions.clear()
        return await self._send({"forget_all": "ticks"})


# ─── Cliente com reconexão automática ────────────────────────────────────────

class DerivAPIClientWithReconnect(DerivAPIClient):
    """
    Extende DerivAPIClient com reconexão automática (backoff exponencial).
    Use este em produção.
    """

    def __init__(self, mode: str = "demo", max_retries: int = 10):
        super().__init__(mode=mode)
        self.max_retries = max_retries
        self._retry_count = 0

    async def connect(self) -> bool:
        delay = 2.0
        for attempt in range(1, self.max_retries + 1):
            self._retry_count = attempt
            ok = await super().connect()
            if ok:
                self._retry_count = 0
                return True
            logger.warning(
                f"Tentativa {attempt}/{self.max_retries} falhou. "
                f"Aguardando {delay:.0f}s..."
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120.0)

        logger.error(f"Falha após {self.max_retries} tentativas.")
        return False

    async def _ensure_connected(self) -> None:
        """Reconecta silenciosamente se a conexão caiu."""
        if not self.ws or not self.ws.open:
            logger.info("Reconectando...")
            await self.connect()

    async def _send(self, payload: Dict, timeout: float = 30.0) -> Dict:
        await self._ensure_connected()
        return await super()._send(payload, timeout=timeout)


# ─── Alias de compatibilidade ─────────────────────────────────────────────────
DerivClient = DerivAPIClientWithReconnect
