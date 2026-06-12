"""
oauth.py — Fluxo OAuth 2.0 para a Deriv API

Permite autenticar usuários via OAuth 2.0 (Authorization Code Flow).
Gerencie tokens de acesso e refresh usando o App ID alfanumérico.

Docs: https://developers.deriv.com/docs/oauth
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ─── Configuração OAuth ───────────────────────────────────────────────────────
DERIV_APP_ID      = os.getenv("DERIV_APP_ID", "")
DERIV_APP_SECRET  = os.getenv("DERIV_APP_SECRET", "")   # opcional para PKCE
OAUTH_REDIRECT    = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/callback")

OAUTH_AUTHORIZE_URL = "https://oauth.deriv.com/oauth2/authorize"
OAUTH_TOKEN_URL     = "https://oauth.deriv.com/oauth2/token"
OAUTH_ACCOUNTS_URL  = "https://api.deriv.com/api/accounts"

# Escopos disponíveis na Deriv
SCOPES_ALL = ["read", "trade", "trading_information", "payments", "admin"]
SCOPES_DEFAULT = ["read", "trade", "trading_information"]


# ─── Token de acesso ──────────────────────────────────────────────────────────

@dataclass
class OAuthToken:
    """Representa um token OAuth 2.0 da Deriv."""
    access_token:  str
    token_type:    str = "Bearer"
    expires_in:    int = 3600
    refresh_token: str = ""
    scopes:        List[str] = field(default_factory=list)
    loginid:       str = ""
    issued_at:     float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        """Retorna True se o token expirou (com margem de 60s)."""
        return time.time() >= (self.issued_at + self.expires_in - 60)

    @property
    def expires_at_str(self) -> str:
        import datetime
        ts = self.issued_at + self.expires_in
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict:
        return {
            "access_token":  self.access_token,
            "token_type":    self.token_type,
            "expires_in":    self.expires_in,
            "refresh_token": self.refresh_token,
            "scopes":        self.scopes,
            "loginid":       self.loginid,
            "issued_at":     self.issued_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "OAuthToken":
        return cls(
            access_token  = data["access_token"],
            token_type    = data.get("token_type", "Bearer"),
            expires_in    = data.get("expires_in", 3600),
            refresh_token = data.get("refresh_token", ""),
            scopes        = data.get("scopes", []),
            loginid       = data.get("loginid", ""),
            issued_at     = data.get("issued_at", time.time()),
        )


# ─── Gerenciador de estado PKCE ───────────────────────────────────────────────

@dataclass
class PKCEState:
    """Estado temporário para o fluxo PKCE (Code Verifier + State)."""
    state:          str = field(default_factory=lambda: secrets.token_urlsafe(32))
    code_verifier:  str = field(default_factory=lambda: secrets.token_urlsafe(64))

    @property
    def code_challenge(self) -> str:
        """SHA-256 do code_verifier, codificado em Base64url."""
        import base64
        digest = hashlib.sha256(self.code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# ─── Cliente OAuth ────────────────────────────────────────────────────────────

class DerivOAuthClient:
    """
    Implementa o fluxo Authorization Code com PKCE para a Deriv API.

    Fluxo:
        1. Gere a URL de autorização: get_authorization_url()
        2. Redirecione o usuário para a URL
        3. Após o callback com ?code=..., chame: exchange_code_for_token(code, state)
        4. Use o access_token para autenticar no WebSocket via DerivAPIClient

    Exemplo (servidor local de callback):
        oauth = DerivOAuthClient()
        url, pkce = oauth.get_authorization_url()
        print(f"Acesse: {url}")
        # ... servidor recebe o callback ...
        token = await oauth.exchange_code_for_token(code, state, pkce)
        print(f"Token: {token.access_token[:12]}...")
    """

    def __init__(
        self,
        app_id: str = DERIV_APP_ID,
        redirect_uri: str = OAUTH_REDIRECT,
        scopes: Optional[List[str]] = None,
    ):
        if not app_id:
            raise EnvironmentError(
                "DERIV_APP_ID não encontrado no .env\n"
                "Gere em: https://developers.deriv.com"
            )
        self.app_id       = app_id
        self.redirect_uri = redirect_uri
        self.scopes       = scopes or SCOPES_DEFAULT
        self._tokens:     Dict[str, OAuthToken] = {}  # loginid → OAuthToken
        self._pkce_store: Dict[str, PKCEState]  = {}  # state → PKCEState

    # ─── Passo 1: Gerar URL de autorização ───────────────────────────────────

    def get_authorization_url(
        self,
        extra_scopes: Optional[List[str]] = None,
        use_pkce: bool = True,
    ) -> tuple[str, PKCEState]:
        """
        Retorna (url, pkce_state).
        Guarde pkce_state para usar no exchange_code_for_token().
        """
        pkce = PKCEState()
        self._pkce_store[pkce.state] = pkce

        scopes = list(set(self.scopes + (extra_scopes or [])))
        params = {
            "app_id":        self.app_id,
            "redirect_uri":  self.redirect_uri,
            "response_type": "code",
            "scope":         " ".join(scopes),
            "state":         pkce.state,
        }

        if use_pkce:
            params["code_challenge"]        = pkce.code_challenge
            params["code_challenge_method"] = "S256"

        url = f"{OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
        logger.info(f"URL OAuth gerada | scopes={scopes} | state={pkce.state[:8]}...")
        return url, pkce

    # ─── Passo 2: Trocar code por token ──────────────────────────────────────

    async def exchange_code_for_token(
        self,
        code: str,
        state: str,
        pkce: Optional[PKCEState] = None,
    ) -> OAuthToken:
        """
        Troca o authorization code por um access_token.
        Valida o state para prevenir CSRF.
        """
        # Valida state (anti-CSRF)
        stored_pkce = pkce or self._pkce_store.pop(state, None)
        if stored_pkce is None:
            raise ValueError(
                f"State inválido ou expirado: '{state[:12]}...'. "
                "Possível ataque CSRF."
            )
        if stored_pkce.state != state:
            raise ValueError("State não confere. Requisição rejeitada.")

        payload = {
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": self.redirect_uri,
            "app_id":       self.app_id,
        }

        if stored_pkce:
            payload["code_verifier"] = stored_pkce.code_verifier

        logger.info("Trocando authorization code por access_token...")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                OAUTH_TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                data = await resp.json()

                if resp.status != 200 or data.get("error"):
                    err = data.get("error", {}) if isinstance(data.get("error"), dict) else data
                    raise PermissionError(
                        f"Falha ao obter token: [{resp.status}] {err}"
                    )

        token = OAuthToken(
            access_token  = data["access_token"],
            token_type    = data.get("token_type", "Bearer"),
            expires_in    = int(data.get("expires_in", 3600)),
            refresh_token = data.get("refresh_token", ""),
            scopes        = data.get("scope", "").split() if data.get("scope") else self.scopes,
        )

        logger.info(
            f"✓ Token obtido | expira em {token.expires_in}s "
            f"({token.expires_at_str}) | scopes={token.scopes}"
        )
        return token

    # ─── Refresh de token ─────────────────────────────────────────────────────

    async def refresh_access_token(self, token: OAuthToken) -> OAuthToken:
        """Renova o access_token usando o refresh_token."""
        if not token.refresh_token:
            raise ValueError("Token não possui refresh_token.")

        payload = {
            "grant_type":    "refresh_token",
            "refresh_token": token.refresh_token,
            "app_id":        self.app_id,
        }

        logger.info("Renovando access_token via refresh_token...")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                OAUTH_TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                data = await resp.json()

                if resp.status != 200 or data.get("error"):
                    raise PermissionError(
                        f"Falha ao renovar token: [{resp.status}] {data}"
                    )

        new_token = OAuthToken(
            access_token  = data["access_token"],
            token_type    = data.get("token_type", "Bearer"),
            expires_in    = int(data.get("expires_in", 3600)),
            refresh_token = data.get("refresh_token", token.refresh_token),
            scopes        = token.scopes,
            loginid       = token.loginid,
        )

        logger.info(f"✓ Token renovado | expira em {new_token.expires_in}s")
        return new_token

    # ─── Autorefresh ──────────────────────────────────────────────────────────

    async def get_valid_token(
        self, token: OAuthToken, auto_refresh: bool = True
    ) -> OAuthToken:
        """
        Retorna o token se ainda válido, ou renova automaticamente.
        Use antes de cada chamada sensível à autenticação.
        """
        if not token.is_expired:
            return token

        logger.info("Token expirado. Renovando automaticamente...")
        if auto_refresh and token.refresh_token:
            return await self.refresh_access_token(token)

        raise PermissionError(
            "Token expirado e sem refresh_token disponível. "
            "Re-autentique o usuário via get_authorization_url()."
        )

    # ─── Revogar token ────────────────────────────────────────────────────────

    async def revoke_token(self, token: OAuthToken) -> bool:
        """Revoga o access_token (logout)."""
        payload = {
            "token":  token.access_token,
            "app_id": self.app_id,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://oauth.deriv.com/oauth2/revoke",
                data=payload,
            ) as resp:
                ok = resp.status in (200, 204)
                if ok:
                    logger.info("Token revogado com sucesso.")
                else:
                    logger.warning(f"Falha ao revogar token: {resp.status}")
                return ok

    # ─── Servidor de callback local (dev/teste) ───────────────────────────────

    async def run_local_callback_server(
        self,
        host: str = "localhost",
        port: int = 8080,
        timeout: int = 120,
    ) -> tuple[str, str]:
        """
        Sobe um servidor HTTP local temporário para capturar o callback OAuth.
        Retorna (code, state) após o redirecionamento.

        Use apenas em desenvolvimento/teste.
        Exemplo:
            url, pkce = oauth.get_authorization_url()
            print(f"Acesse: {url}")
            code, state = await oauth.run_local_callback_server()
            token = await oauth.exchange_code_for_token(code, state, pkce)
        """
        from aiohttp import web

        result: Dict = {}
        event = asyncio.Event()

        async def callback_handler(request: web.Request) -> web.Response:
            result["code"]  = request.rel_url.query.get("code", "")
            result["state"] = request.rel_url.query.get("state", "")
            result["error"] = request.rel_url.query.get("error", "")
            event.set()
            return web.Response(
                text="<h2>✓ Autenticado! Pode fechar esta janela.</h2>",
                content_type="text/html",
            )

        app = web.Application()
        app.router.add_get("/callback", callback_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        logger.info(f"Servidor OAuth escutando em http://{host}:{port}/callback")

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Timeout: nenhum callback recebido em {timeout}s."
            )
        finally:
            await runner.cleanup()

        if result.get("error"):
            raise PermissionError(f"Erro no callback OAuth: {result['error']}")

        return result["code"], result["state"]


# ─── Utilitário: autenticar interativamente (dev/CLI) ─────────────────────────

async def authenticate_interactive(
    scopes: Optional[List[str]] = None,
) -> OAuthToken:
    """
    Fluxo completo de autenticação OAuth 2.0 para uso em CLI/desenvolvimento.
    Abre o browser, aguarda o callback e retorna o token.

    Uso:
        token = await authenticate_interactive()
        # Use token.access_token no DerivAPIClient
    """
    import webbrowser

    oauth  = DerivOAuthClient(scopes=scopes)
    url, pkce = oauth.get_authorization_url()

    print(f"\n{'='*60}")
    print("  Deriv OAuth 2.0 — Autenticação Interativa")
    print(f"{'='*60}")
    print(f"\nAbrindo browser para autenticação...")
    print(f"URL: {url}\n")

    webbrowser.open(url)

    code, state = await oauth.run_local_callback_server()
    token = await oauth.exchange_code_for_token(code, state, pkce)

    print(f"\n✓ Autenticado com sucesso!")
    print(f"  Token: {token.access_token[:12]}...")
    print(f"  Expira: {token.expires_at_str}")
    print(f"  Scopes: {', '.join(token.scopes)}\n")

    return token
