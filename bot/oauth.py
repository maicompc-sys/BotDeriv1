"""
oauth.py — OAuth 2.0 Authorization Code + PKCE para a Deriv API

Suporta App ID alfanumérico (ex: 33wQj4vvambGV9iRyHOTh).
Fluxo: get_authorization_url() → callback com ?code= → exchange_code_for_token()
O access_token resultante é usado no DerivAPIClient para autenticar o WebSocket.

Docs: https://developers.deriv.com/docs/oauth
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import aiohttp
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ─── Configuração ─────────────────────────────────────────────────────────────
DERIV_APP_ID   = os.getenv("DERIV_APP_ID", "")
OAUTH_REDIRECT = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/callback")

OAUTH_AUTHORIZE_URL = "https://oauth.deriv.com/oauth2/authorize"
OAUTH_TOKEN_URL     = "https://oauth.deriv.com/oauth2/token"
OAUTH_REVOKE_URL    = "https://oauth.deriv.com/oauth2/revoke"

# Escopos disponíveis
SCOPES_DEFAULT = ["read", "trade", "trading_information"]
SCOPES_ALL     = ["read", "trade", "trading_information", "payments", "admin"]


# ─── Token OAuth ──────────────────────────────────────────────────────────────

@dataclass
class OAuthToken:
    """Representa um token de acesso OAuth 2.0 da Deriv."""
    access_token:  str
    token_type:    str       = "Bearer"
    expires_in:    int       = 3600
    refresh_token: str       = ""
    scopes:        List[str] = field(default_factory=list)
    loginid:       str       = ""
    issued_at:     float     = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        """True se o token expirou (com margem de 60s)."""
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
    def from_dict(cls, d: Dict) -> "OAuthToken":
        return cls(
            access_token  = d["access_token"],
            token_type    = d.get("token_type", "Bearer"),
            expires_in    = d.get("expires_in", 3600),
            refresh_token = d.get("refresh_token", ""),
            scopes        = d.get("scopes", []),
            loginid       = d.get("loginid", ""),
            issued_at     = d.get("issued_at", time.time()),
        )

    def mask(self) -> str:
        """Token mascarado para logging seguro."""
        return self.access_token[:12] + "..." if self.access_token else "(vazio)"


# ─── Estado PKCE ──────────────────────────────────────────────────────────────

@dataclass
class PKCEState:
    """State + Code Verifier para o fluxo PKCE (anti-CSRF + anti-intercept)."""
    state:         str = field(default_factory=lambda: secrets.token_urlsafe(32))
    code_verifier: str = field(default_factory=lambda: secrets.token_urlsafe(64))

    @property
    def code_challenge(self) -> str:
        """SHA-256 do code_verifier, Base64url sem padding."""
        import base64
        digest = hashlib.sha256(self.code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# ─── Cliente OAuth ────────────────────────────────────────────────────────────

class DerivOAuthClient:
    """
    Fluxo Authorization Code + PKCE para a Deriv API.

    Exemplo de uso completo (CLI/dev):
        oauth = DerivOAuthClient()
        url, pkce = oauth.get_authorization_url()
        print(f"Acesse: {url}")
        code, state = await oauth.run_local_callback_server()
        token = await oauth.exchange_code_for_token(code, state, pkce)
        # Agora use token.access_token no DerivAPIClient

    Exemplo de uso em web app:
        url, pkce = oauth.get_authorization_url()
        # redirecione o usuário para url
        # no endpoint /callback:
        token = await oauth.exchange_code_for_token(
            request.query["code"],
            request.query["state"],
            pkce,  # guardado na sessão
        )
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
        # state → PKCEState (armazenamento em memória; use Redis/DB em produção)
        self._pkce_store: Dict[str, PKCEState] = {}

    # ─── Passo 1: URL de autorização ──────────────────────────────────────────

    def get_authorization_url(
        self,
        extra_scopes: Optional[List[str]] = None,
        use_pkce: bool = True,
    ) -> Tuple[str, PKCEState]:
        """
        Gera a URL para redirecionar o usuário.
        Retorna (url, pkce_state) — guarde pkce_state para o próximo passo.
        """
        pkce = PKCEState()
        self._pkce_store[pkce.state] = pkce

        scopes = sorted(set(self.scopes + (extra_scopes or [])))
        params: Dict = {
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

    # ─── Passo 2: Troca de código por token ───────────────────────────────────

    async def exchange_code_for_token(
        self,
        code: str,
        state: str,
        pkce: Optional[PKCEState] = None,
    ) -> OAuthToken:
        """
        Troca o authorization code por access_token.
        Valida o state para prevenir CSRF.
        """
        stored_pkce = pkce or self._pkce_store.pop(state, None)
        if stored_pkce is None:
            raise ValueError(
                f"State inválido ou expirado: '{state[:12]}...'. "
                "Possível ataque CSRF — rejeitando."
            )
        if stored_pkce.state != state:
            raise ValueError("State não confere. Requisição rejeitada.")

        payload: Dict = {
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  self.redirect_uri,
            "app_id":        self.app_id,
        }
        if stored_pkce:
            payload["code_verifier"] = stored_pkce.code_verifier

        logger.info("Trocando authorization code por access_token...")
        data = await self._post(OAUTH_TOKEN_URL, payload)

        token = OAuthToken(
            access_token  = data["access_token"],
            token_type    = data.get("token_type", "Bearer"),
            expires_in    = int(data.get("expires_in", 3600)),
            refresh_token = data.get("refresh_token", ""),
            scopes        = data.get("scope", "").split() if data.get("scope") else self.scopes,
        )
        logger.info(
            f"✓ Token obtido | expira: {token.expires_at_str} | "
            f"scopes={token.scopes} | token={token.mask()}"
        )
        return token

    # ─── Refresh ──────────────────────────────────────────────────────────────

    async def refresh_access_token(self, token: OAuthToken) -> OAuthToken:
        """Renova o access_token usando o refresh_token."""
        if not token.refresh_token:
            raise ValueError("Token não possui refresh_token.")

        logger.info("Renovando access_token via refresh_token...")
        data = await self._post(OAUTH_TOKEN_URL, {
            "grant_type":    "refresh_token",
            "refresh_token": token.refresh_token,
            "app_id":        self.app_id,
        })

        new_token = OAuthToken(
            access_token  = data["access_token"],
            token_type    = data.get("token_type", "Bearer"),
            expires_in    = int(data.get("expires_in", 3600)),
            refresh_token = data.get("refresh_token", token.refresh_token),
            scopes        = token.scopes,
            loginid       = token.loginid,
        )
        logger.info(f"✓ Token renovado | expira: {new_token.expires_at_str}")
        return new_token

    # ─── Auto-refresh ─────────────────────────────────────────────────────────

    async def get_valid_token(
        self,
        token: OAuthToken,
        auto_refresh: bool = True,
    ) -> OAuthToken:
        """
        Retorna o token se válido, ou renova automaticamente.
        Chame antes de cada requisição sensível.
        """
        if not token.is_expired:
            return token
        logger.info("Token expirado. Renovando automaticamente...")
        if auto_refresh and token.refresh_token:
            return await self.refresh_access_token(token)
        raise PermissionError(
            "Token expirado e sem refresh_token. "
            "Re-autentique via get_authorization_url()."
        )

    # ─── Revogação ────────────────────────────────────────────────────────────

    async def revoke_token(self, token: OAuthToken) -> bool:
        """Revoga o access_token (logout)."""
        try:
            await self._post(OAUTH_REVOKE_URL, {
                "token":  token.access_token,
                "app_id": self.app_id,
            })
            logger.info("Token revogado com sucesso.")
            return True
        except Exception as e:
            logger.warning(f"Falha ao revogar token: {e}")
            return False

    # ─── Servidor de callback local ───────────────────────────────────────────

    async def run_local_callback_server(
        self,
        host: str = "localhost",
        port: int = 8080,
        timeout: int = 120,
    ) -> Tuple[str, str]:
        """
        Servidor HTTP local temporário para capturar o callback OAuth.
        Retorna (code, state). Use apenas em desenvolvimento/CLI.

        Exemplo:
            url, pkce = oauth.get_authorization_url()
            print(f"Acesse: {url}")
            code, state = await oauth.run_local_callback_server()
            token = await oauth.exchange_code_for_token(code, state, pkce)
        """
        from aiohttp import web

        result: Dict = {}
        done = asyncio.Event()

        async def handler(req: web.Request) -> web.Response:
            result["code"]  = req.rel_url.query.get("code", "")
            result["state"] = req.rel_url.query.get("state", "")
            result["error"] = req.rel_url.query.get("error", "")
            done.set()
            return web.Response(
                text="<h2>✓ Autenticado! Pode fechar esta janela.</h2>",
                content_type="text/html",
            )

        app = web.Application()
        app.router.add_get("/callback", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        logger.info(f"Aguardando callback em http://{host}:{port}/callback")

        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Timeout: nenhum callback em {timeout}s. Tente novamente."
            )
        finally:
            await runner.cleanup()

        if result.get("error"):
            raise PermissionError(f"Erro no callback OAuth: {result['error']}")

        return result["code"], result["state"]

    # ─── HTTP helper ──────────────────────────────────────────────────────────

    async def _post(self, url: str, payload: Dict) -> Dict:
        """POST form-encoded com tratamento de erro unificado."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status not in (200, 201) or data.get("error"):
                    raise PermissionError(
                        f"[{resp.status}] {data.get('error', data)}"
                    )
                return data


# ─── Autenticação interativa (CLI/dev) ────────────────────────────────────────

async def authenticate_interactive(
    scopes: Optional[List[str]] = None,
) -> OAuthToken:
    """
    Fluxo completo OAuth 2.0 para CLI/desenvolvimento.
    Abre o browser, aguarda o callback e retorna o token.

    Uso:
        token = await authenticate_interactive()
        # use token.access_token no DerivAPIClient
    """
    import webbrowser

    oauth = DerivOAuthClient(scopes=scopes)
    url, pkce = oauth.get_authorization_url()

    print(f"\n{'='*60}")
    print("  Deriv OAuth 2.0 — Autenticação Interativa")
    print(f"{'='*60}")
    print(f"\nAbrindo browser...")
    print(f"URL: {url}\n")
    webbrowser.open(url)

    code, state = await oauth.run_local_callback_server()
    token = await oauth.exchange_code_for_token(code, state, pkce)

    print(f"\n✓ Autenticado!")
    print(f"  Token: {token.mask()}")
    print(f"  Expira: {token.expires_at_str}")
    print(f"  Scopes: {', '.join(token.scopes)}\n")
    return token
