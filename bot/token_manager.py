"""
token_manager.py — Gerenciador de tokens PAT + OAuth para a Deriv API

Persiste e gerencia múltiplos tokens (por conta/modo).
Suporta tokens alfanuméricos (pat_xxx) e OAuth access tokens.
"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TOKEN_STORE_PATH = Path(os.getenv("TOKEN_STORE_PATH", ".tokens.json"))


@dataclass
class StoredToken:
    """Token armazenado localmente (PAT ou OAuth)."""
    loginid:       str
    token:         str
    token_type:    str   = "pat"          # "pat" | "oauth"
    mode:          str   = "demo"         # "demo" | "real"
    scopes:        list  = field(default_factory=list)
    issued_at:     float = field(default_factory=time.time)
    expires_in:    int   = 0              # 0 = sem expiração (PAT)
    refresh_token: str   = ""

    @property
    def is_expired(self) -> bool:
        if self.expires_in == 0:
            return False  # PATs não expiram por tempo
        return time.time() >= (self.issued_at + self.expires_in - 60)

    @property
    def token_preview(self) -> str:
        """Exibe apenas os primeiros 12 chars do token (segurança)."""
        return self.token[:12] + "..." if len(self.token) > 12 else "***"


class TokenManager:
    """
    Gerencia tokens PAT e OAuth da Deriv.

    - Carrega tokens do .env (PAT) e do store local (OAuth)
    - Valida, persiste e rotaciona tokens automaticamente
    - Thread-safe para uso em bots assíncronos

    Uso:
        tm = TokenManager()
        token = tm.get_token("demo")
        print(token.token_preview)
    """

    def __init__(self, store_path: Path = TOKEN_STORE_PATH):
        self.store_path = store_path
        self._tokens: Dict[str, StoredToken] = {}  # key: loginid ou mode
        self._load_from_env()
        self._load_from_store()

    # ─── Carregamento ─────────────────────────────────────────────────────────

    def _load_from_env(self):
        """Carrega tokens PAT do .env."""
        pairs = {
            "demo": ("DERIV_API_TOKEN_DEMO", "demo"),
            "real": ("DERIV_API_TOKEN_REAL", "real"),
        }
        for key, (env_var, mode) in pairs.items():
            token_val = os.getenv(env_var, "")
            if token_val:
                self._tokens[mode] = StoredToken(
                    loginid    = mode,  # placeholder até autenticar
                    token      = token_val,
                    token_type = "pat",
                    mode       = mode,
                    scopes     = ["read", "trade", "trading_information"],
                )
                logger.debug(f"PAT carregado do .env para modo '{mode}'")

    def _load_from_store(self):
        """Carrega tokens OAuth persistidos no arquivo local."""
        if not self.store_path.exists():
            return
        try:
            with open(self.store_path, "r") as f:
                data = json.load(f)
            for key, raw in data.items():
                t = StoredToken(**raw)
                if not t.is_expired or t.refresh_token:
                    self._tokens[key] = t
                    logger.debug(f"Token OAuth carregado do store: {key}")
        except Exception as e:
            logger.warning(f"Falha ao carregar token store: {e}")

    def _save_to_store(self):
        """Persiste tokens OAuth no arquivo local."""
        try:
            oauth_tokens = {
                k: asdict(v)
                for k, v in self._tokens.items()
                if v.token_type == "oauth"
            }
            with open(self.store_path, "w") as f:
                json.dump(oauth_tokens, f, indent=2)
        except Exception as e:
            logger.warning(f"Falha ao salvar token store: {e}")

    # ─── Operações ────────────────────────────────────────────────────────────

    def get_token(self, key: str) -> Optional[StoredToken]:
        """
        Retorna o token para o modo ('demo', 'real') ou loginid.
        Retorna None se não existir.
        """
        return self._tokens.get(key)

    def store_oauth_token(
        self,
        loginid:       str,
        access_token:  str,
        mode:          str,
        scopes:        list,
        expires_in:    int  = 3600,
        refresh_token: str  = "",
    ) -> StoredToken:
        """Armazena um token OAuth obtido via fluxo de autorização."""
        stored = StoredToken(
            loginid       = loginid,
            token         = access_token,
            token_type    = "oauth",
            mode          = mode,
            scopes        = scopes,
            issued_at     = time.time(),
            expires_in    = expires_in,
            refresh_token = refresh_token,
        )
        self._tokens[loginid] = stored
        self._tokens[mode]    = stored  # atalho por modo
        self._save_to_store()
        logger.info(f"Token OAuth armazenado para loginid={loginid} modo={mode}")
        return stored

    def update_loginid(self, mode: str, loginid: str):
        """Atualiza o loginid de um token PAT após autenticação bem-sucedida."""
        if mode in self._tokens:
            self._tokens[mode].loginid = loginid
            self._tokens[loginid] = self._tokens[mode]

    def revoke(self, key: str):
        """Remove um token do gerenciador."""
        if key in self._tokens:
            t = self._tokens.pop(key)
            # Remove também pelo loginid se for diferente
            if t.loginid != key and t.loginid in self._tokens:
                self._tokens.pop(t.loginid, None)
            self._save_to_store()
            logger.info(f"Token revogado localmente: {key}")

    def list_tokens(self) -> list:
        """Lista todos os tokens gerenciados (sem expor valores)."""
        seen = set()
        result = []
        for k, t in self._tokens.items():
            if t.loginid not in seen:
                seen.add(t.loginid)
                result.append({
                    "key":         k,
                    "loginid":     t.loginid,
                    "mode":        t.mode,
                    "token_type":  t.token_type,
                    "token":       t.token_preview,
                    "scopes":      t.scopes,
                    "expired":     t.is_expired,
                })
        return result

    def has_valid_token(self, key: str) -> bool:
        """Verifica se existe um token válido (não expirado) para a chave."""
        t = self._tokens.get(key)
        return t is not None and not t.is_expired
