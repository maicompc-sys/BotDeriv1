"""
main.py — Entry point do BotDeriv1

Uso:
    python main.py          # modo demo (padrão)
    python main.py demo     # modo demo (PAT)
    python main.py real     # modo real  (PAT)
    python main.py oauth    # fluxo OAuth 2.0 interativo

Configure o .env antes de rodar:
    cp .env.example .env
    # Preencha DERIV_APP_ID e DERIV_API_TOKEN_DEMO / DERIV_API_TOKEN_REAL

Gere credenciais em:
    App ID  → https://developers.deriv.com
    PAT     → https://app.deriv.com/account/api-token
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

DEFAULT_MODE = os.getenv("DEFAULT_MODE", "demo").lower()


async def run_pat(mode: str = DEFAULT_MODE) -> None:
    """
    Autenticação via PAT — lê credenciais do .env automaticamente.
    """
    print(f"\n{'='*54}")
    print(f"  BotDeriv1  |  Modo: {mode.upper()}  |  Auth: PAT")
    print(f"{'='*54}\n")

    client = DerivAPIClientWithReconnect(mode=mode)

    try:
        connected = await client.connect()
    except EnvironmentError as e:
        print(f"\nERRO DE CONFIGURAÇÃO:\n  {e}")
        print("\n  Passos:\n  1. cp .env.example .env")
        print("  2. Preencha DERIV_APP_ID e DERIV_API_TOKEN_DEMO/REAL")
        print("  3. python main.py demo")
        return

    if not connected:
        print("\nFalha na conexão. Verifique as credenciais no .env.")
        return

    # ── Informações da conta ──────────────────────────────────────────────────
    print(f"Conectado!")
    print(f"  loginid  : {client.get_loginid()}")
    print(f"  saldo    : {client.get_balance()} {client.get_currency()}")
    print(f"  tipo     : {'DEMO (Virtual)' if client.is_demo() else 'REAL'}")
    print(f"  token    : {client.get_token_info()['format']}")

    ok = await client.ping()
    print(f"  ping     : {'pong OK' if ok else 'FALHOU'}\n")

    # ── Símbolos disponíveis ──────────────────────────────────────────────────
    print("Símbolos sintéticos disponíveis:")
    symbols = await client.get_active_symbols("synthetic_index")
    if symbols:
        for s in symbols[:8]:
            print(f"  {s.get('symbol', ''):18} {s.get('display_name', '')}")
        if len(symbols) > 8:
            print(f"  ... e mais {len(symbols) - 8} símbolos")
    else:
        print("  (nenhum símbolo retornado)")
    print()

    # ── Saldo em tempo real ───────────────────────────────────────────────────
    bal_res = await client.get_account_balance()
    if not bal_res.get("error"):
        b = bal_res.get("balance", {})
        print(f"Saldo: {b.get('balance')} {b.get('currency')}")

    # ── Exemplo: proposta de trade (descomente para testar) ───────────────────
    # proposal = await client.get_price_proposal(
    #     symbol="R_100",
    #     contract_type="CALL",
    #     duration=5,
    #     duration_unit="t",
    #     stake=1.0,
    # )
    # if not proposal.get("error"):
    #     p = proposal["proposal"]
    #     print(f"Proposta: {p['id']} | payout={p['payout']} | ask={p['ask_price']}")
    #     # Comprar:
    #     # result = await client.buy_contract(p["id"], price=p["ask_price"])

    # ── Exemplo: troca DEMO <-> REAL (descomente para testar) ─────────────────
    # print("\nTrocando para conta REAL...")
    # await client.switch_account("real")
    # print(f"  loginid : {client.get_loginid()}")
    # print(f"  saldo   : {client.get_balance()} {client.get_currency()}")

    # ── Estratégia principal (descomente quando implementar) ──────────────────
    # from bot.strategies import run_strategy
    # await run_strategy(client)

    await client.disconnect()
    print("\nDesconectado.")


async def run_oauth() -> None:
    """
    Autenticação via OAuth 2.0 (abre browser, aguarda callback).
    Útil para obter access_token para uso no WebSocket.
    """
    from bot.oauth import authenticate_interactive
    from bot.token_manager import TokenManager

    print(f"\n{'='*54}")
    print("  BotDeriv1  |  Autenticação OAuth 2.0 + PKCE")
    print(f"{'='*54}\n")

    token = await authenticate_interactive()

    manager = TokenManager()
    manager.store_token(token, loginid=token.loginid or "oauth_user")

    print("Tokens gerenciados:")
    for lid, info in manager.summary().items():
        print(
            f"  [{lid}] expira em {info['expires_in_s']}s "
            f"| scopes={info['scopes']} "
            f"| refresh={'sim' if info['has_refresh'] else 'não'}"
        )

    print(
        "\nDica: copie o access_token para DERIV_API_TOKEN_DEMO ou "
        "DERIV_API_TOKEN_REAL no .env para usar no modo PAT."
    )


if __name__ == "__main__":
    args     = sys.argv[1:]
    mode_arg = (args[0] if args else DEFAULT_MODE).lower()

    if mode_arg == "oauth":
        asyncio.run(run_oauth())
    elif mode_arg in ("demo", "real"):
        asyncio.run(run_pat(mode_arg))
    else:
        print("Uso: python main.py [demo | real | oauth]")
        print("  demo   — autentica via PAT, conta virtual")
        print("  real   — autentica via PAT, conta real")
        print("  oauth  — fluxo OAuth 2.0 interativo (abre browser)")
        sys.exit(1)
