"""
main.py — Entry point do BotDeriv1
Autentica automaticamente e permite escolher modo DEMO ou REAL.
"""

import asyncio
import logging
import os
from dotenv import load_dotenv
from bot.deriv_api import DerivClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

DEFAULT_MODE = os.getenv("DEFAULT_MODE", "demo")


async def run(mode: str = DEFAULT_MODE):
    print(f"\n{'='*50}")
    print(f"  BotDeriv1 | Modo: {mode.upper()}")
    print(f"{'='*50}\n")

    client = DerivClient(mode=mode)

    # ── Conecta e autentica automaticamente ──────────────────
    connected = await client.connect()
    if not connected:
        print("Falha na conexão. Verifique o .env e tente novamente.")
        return

    print(f"\n✓ Conta:  {client.get_loginid()}")
    print(f"✓ Saldo:  {client.get_balance()} {client.get_currency()}")
    print(f"✓ Tipo:   {'DEMO (Virtual)' if client.is_demo() else 'REAL'}\n")

    # ── Exemplo: trocar entre demo e real em tempo real ───────
    # await client.switch_account("real")
    # await client.switch_account("demo")

    # ── Aqui vai a lógica principal do bot ───────────────────
    # from bot.strategies import run_strategy
    # await run_strategy(client)

    await client.disconnect()


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODE
    if mode not in ("demo", "real"):
        print("Uso: python main.py [demo|real]")
        import sys; sys.exit(1)
    asyncio.run(run(mode))
