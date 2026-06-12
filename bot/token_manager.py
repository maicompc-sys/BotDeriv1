"""
token_manager.py — Gerenciador de tokens OAuth 2.0 + PAT para a Deriv API

Centraliza o ciclo de vida dos tokens:
- Armazena tokens em memória (use DB/Redis em produção)
- Auto-refresh antes do vencimento
- Integração com DerivAPIClient (WebSocket) e DerivOAuthClient (OAuth)
- Suporta múltiplas contas simultâneas
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

from dotenv import load_dotenv

from bot.oauth import DerivOAuthClient, OAuthToken

load_dotenv()
logger = logging.getLogger(__name__)

# Margem de renovação antecipada: renovar 5 min antes de expirar
REFRESH_MARGIN_SECONDS = 300


class TokenManager:
    """
    Gerencia tokens OAuth e PAT para múltiplas contas Deriv.

    Responsabilidades:
    - Armazenar access_token e refresh_token por loginid
    - Auto-renovar tokens expirados
    - Fornecer tokens válidos para o DerivAPIClient
    - Manter histórico de rotação (auditoria)

    Uso:
        manager = TokenManager()
        token = manager.store_token(oauth_token)
        valid_token = await manager.get_valid_token(loginid)
    """

    def __init__(self, oauth_client: Optional[DerivOAuthClient] = None):
        self._oauth = oauth_client or DerivOAuthClient()
        # loginid → OAuthToken
        self._tokens: Dict[str, OAuthToken] = {}
        # loginid → lista de tokens anteriores (auditoria)
        self._history: Dict[str, List[Dict]] = {}
        self._lock = asyncio.Lock()

    # ─── Armazenamento ────────────────────────────────────────────────────────

    def store_token(self, token: OAuthToken, loginid: Optional[str] = None) -> OAuthToken:
        """
        Armazena um token indexado por loginid.
        Se loginid não fornecido, usa token.loginid.
        """
        lid = loginid or token.loginid or "default"
        token.loginid = lid

        # Salva token anterior no histórico
        if lid in self._tokens:
            old = self._tokens[lid]
            self._history.setdefault(lid, []).append({
                "token_prefix": old.access_token[:12] + "...",
                "issued_at":    old.issued_at,
                "rotated_at":   time.time(),
            })

        self._tokens[lid] = token
        logger.info(
            f"Token armazenado | loginid={lid} | "
            f"expira={token.expires_at_str} | scopes={token.scopes}"
        )
        return token

    def get_token(self, loginid: str = "default") -> Optional[OAuthToken]:
        """Retorna o token armazenado para um loginid (sem renovar)."""
        return self._tokens.get(loginid)

    def remove_token(self, loginid: str = "default") -> bool:
        """Remove o token de um loginid (logout)."""
        removed = self._tokens.pop(loginid, None)
        if removed:
            logger.info(f"Token removido | loginid={loginid}")
        return removed is not None

    def list_accounts(self) -> List[str]:
        """Lista todos os loginids com token armazenado."""
        return list(self._tokens.keys())

    # ─── Obtenção de token válido ─────────────────────────────────────────────

    async def get_valid_token(
        self,
        loginid: str = "default",
        auto_refresh: bool = True,
    ) -> OAuthToken:
        """
        Retorna um token válido para o loginid.
        Renova automaticamente se estiver próximo do vencimento.

        Raises:
            KeyError: loginid não encontrado
            PermissionError: token expirado sem refresh_token
        """
        async with self._lock:
            token = self._tokens.get(loginid)
            if token is None:
                raise KeyError(
                    f"Nenhum token para loginid='{loginid}'. "
                    "Autentique via OAuth primeiro."
                )

            # Verifica expiração antecipada
            time_left = (token.issued_at + token.expires_in) - time.time()
            if time_left <= REFRESH_MARGIN_SECONDS:
                if auto_refresh and token.refresh_token:
                    logger.info(
                        f"Token de '{loginid}' expira em {time_left:.0f}s. "
                        "Renovando..."
                    )
                    new_token = await self._oauth.refresh_access_token(token)
                    new_token.loginid = loginid
                    self.store_token(new_token, loginid)
                    return new_token
                elif token.is_expired:
                    raise PermissionError(
                        f"Token de '{loginid}' expirado e sem refresh_token. "
                        "Re-autentique via OAuth."
                    )

            return token

    # ─── Auto-refresh em background ───────────────────────────────────────────

    async def start_auto_refresh(
        self,
        check_interval: int = 60,
    ) -> asyncio.Task:
        """
        Inicia uma task em background que renova tokens antes do vencimento.
        Retorna a task para cancelar quando necessário.

        Uso:
            task = await manager.start_auto_refresh()
            # Para parar:
            task.cancel()
        """
        async def _loop():
            while True:
                await asyncio.sleep(check_interval)
                for lid in list(self._tokens.keys()):
                    try:
                        await self.get_valid_token(lid, auto_refresh=True)
                    except Exception as e:
                        logger.warning(f"Auto-refresh falhou para '{lid}': {e}")

        task = asyncio.create_task(_loop())
        logger.info(f"Auto-refresh iniciado (intervalo={check_interval}s)")
        return task

    # ─── Revogação ────────────────────────────────────────────────────────────

    async def revoke_token(
        self,
        loginid: str = "default",
    ) -> bool:
        """Revoga e remove o token de um loginid."""
        token = self._tokens.get(loginid)
        if not token:
            logger.warning(f"Nenhum token para revogar: loginid={loginid}")
            return False
        ok = await self._oauth.revoke_token(token)
        if ok:
            self.remove_token(loginid)
        return ok

    async def revoke_all(self) -> Dict[str, bool]:
        """Revoga todos os tokens armazenados."""
        results = {}
        for lid in list(self._tokens.keys()):
            results[lid] = await self.revoke_token(lid)
        return results

    # ─── Auditoria ────────────────────────────────────────────────────────────

    def get_history(self, loginid: str = "default") -> List[Dict]:
        """Histórico de rotação de tokens para auditoria."""
        return self._history.get(loginid, [])

    def summary(self) -> Dict:
        """Resumo de todos os tokens gerenciados."""
        now = time.time()
        result = {}
        for lid, tok in self._tokens.items():
            expires_in = (tok.issued_at + tok.expires_in) - now
            result[lid] = {
                "loginid":       lid,
                "token_prefix":  tok.access_token[:12] + "...",
                "expires_in_s":  int(expires_in),
                "expires_at":    tok.expires_at_str,
                "is_expired":    tok.is_expired,
                "has_refresh":   bool(tok.refresh_token),
                "scopes":        tok.scopes,
            }
        return result
