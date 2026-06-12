"""
main.py — Entry point do BotDeriv1

Uso:
    python main.py         # modo demo (padrão)
    python main.py demo    # modo demo
    python main.py real    # modo real

Autentica automaticamente via PAT configurado no .env.
Para OAuth 2.0 interativo, use: python main.py oauth
"""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

from bot.deriv_api import DerivAPIClientWithReconnect

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

DEFAULT_MODE = os.getenv("DEFAULT_MODE", "demo")


async def run_pat(mode: str = DEFAULT_MODE) -> None:
    """
    Autenticação via PAT (Personal Access Token).
    Lê as credenciais do .env automaticamente.
    """
    print(f"\n{'='*52}")
    print(f"  BotDeriv1 | Autenticação PAT | Modo: {mode.upper()}")
    print(f"{'='*52}\n")

    client = DerivAPIClientWithReconnect(mode=mode)

    connected = await client.connect()
    if not connected:
        print("\n✗ Falha na conexão. Verifique o .env:")
        print("  DERIV_APP_ID=<app_id_alfanumérico>")
        print("  DERIV_API_TOKEN_DEMO=pat_<64hex>")
        print("  DERIV_API_TOKEN_REAL=pat_<64hex>")
        print("  → Gere em: https://developers.deriv.com")
        return

    # Informações da conta
    print(f"✓ Conectado!")
    print(f"  loginid : {client.get_loginid()}")
    print(f"  saldo   : {client.get_balance()} {client.get_currency()}")
    print(f"  tipo    : {'DEMO (Virtual)' if client.is_demo() else 'REAL'}")
    print(f"  token   : {client.get_token_info()['format']}\n")

    # Ping
    ok = await client.ping()
    print(f"  ping    : {'✓ pong' if ok else '✗ falhou'}\n")

    # Exemplo: símbolos disponíveis
    print("Carregando símbolos sintéticos...")
    symbols = await client.get_active_symbols("synthetic_index")
    if symbols:
        print(f"  {len(symbols)} símbolos disponíveis")
        for s in symbols[:5]:
            print(f"  · {s.get('symbol'):15} {s.get('display_name')}")
        if len(symbols) > 5:
            print(f"  ... e mais {len(symbols) - 5}")
    print()

    # Exemplo: saldo em tempo real
    bal_res = await client.get_account_balance()
    if not bal_res.get("error"):
        b = bal_res.get("balance", {})
        print(f"Saldo atual: {b.get('balance')} {b.get('currency')}")

    # ── Troca DEMO ↔ REAL (descomente para testar) ────────────────────────────
    # print("\nTrocando para conta REAL...")
    # await client.switch_account("real")
    # print(f"Novo loginid: {client.get_loginid()}")
    # print(f"Novo saldo:   {client.get_balance()} {client.get_currency()}")

    # ── Lógica principal do bot ───────────────────────────────────────────────
    # from bot.strategies import run_strategy
    # await run_strategy(client)

    await client.disconnect()
    print("\n✓ Desconectado.")


async def run_oauth() -> None:
    """
    Autenticação via OAuth 2.0 (abre browser, aguarda callback).
    Use em desenvolvimento/testes de fluxo OAuth.
    """
    from bot.oauth import authenticate_interactive
    from bot.token_manager import TokenManager

    print(f"\n{'='*52}")
    print("  BotDeriv1 | Autenticação OAuth 2.0")
    print(f"{'='*52}\n")

    token = await authenticate_interactive()

    manager = TokenManager()
    manager.store_token(token, loginid=token.loginid or "oauth_user")

    print("Tokens gerenciados:")
    for lid, info in manager.summary().items():
        print(f"  [{lid}] expira em {info['expires_in_s']}s | scopes={info['scopes']}")

    # Usar token no WebSocket:
    # O access_token obtido via OAuth pode ser enviado diretamente
    # no payload {"authorize": token.access_token} via DerivAPIClient._authorize()
    # Para isso, configure DERIV_API_TOKEN_DEMO ou REAL no .env com o access_token.


if __name__ == "__main__":
    args = sys.argv[1:]
    mode_arg = (args[0] if args else DEFAULT_MODE).lower()

    if mode_arg == "oauth":
        asyncio.run(run_oauth())
    elif mode_arg in ("demo", "real"):
        asyncio.run(run_pat(mode_arg))
    else:
        print(f"Uso: python main.py [demo|real|oauth]")
        print(f"  demo   — autenticação PAT, conta virtual")
        print(f"  real   — autenticação PAT, conta real")
        print(f"  oauth  — fluxo OAuth 2.0 interativo (abre browser)")
        sys.exit(1)
