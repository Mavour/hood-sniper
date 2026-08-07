#!/usr/bin/env python3
"""hoodsniper v2 — multi-group sniper Robinhood Chain.

Mirror sinyal: akun Telegram sendiri (Telethon, QR login) — tanpa add bot ke group.
Control panel: bot BotFather (inline keyboard, gaya trading bot).

Pemakaian:
  python bot.py login   # sekali: login akun Telegram via QR (interaktif)
  python bot.py run     # jalankan (dipakai PM2)
"""
import asyncio
import sys
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from sniper import config, db, engine, listener, control_bot
from sniper.trader import Trader


async def qr_login(client):
    import qrcode
    qr = await client.qr_login()
    print("\nScan QR dari HP: Settings → Devices → Link Desktop Device\n")
    while not await client.is_user_authorized():
        q = qrcode.QRCode()
        q.add_data(qr.url)
        q.print_ascii(invert=True)
        print(f"(atau buka URL: {qr.url})")
        try:
            await qr.wait(30)
        except SessionPasswordNeededError:
            import getpass
            pw = getpass.getpass("Akun pakai 2FA. Password: ")
            await client.sign_in(password=pw)
        except asyncio.TimeoutError:
            qr = await client.qr_login()
    me = await client.get_me()
    print(f"\n✅ Login sukses: {me.first_name} (@{me.username})")


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    user = TelegramClient(config.SESSION_NAME, config.TG_API_ID, config.TG_API_HASH)
    await user.connect()

    if mode == "login":
        if await user.is_user_authorized():
            me = await user.get_me()
            print(f"Sudah login sebagai {me.first_name}. Session OK.")
        else:
            await qr_login(user)
        await user.disconnect()
        return

    if not await user.is_user_authorized():
        raise SystemExit("Belum login. Jalankan dulu: python bot.py login")

    config.validate()
    db.init()
    trader = Trader()

    bot = TelegramClient("hoodsniper_bot", config.TG_API_ID, config.TG_API_HASH)
    await bot.start(bot_token=config.BOT_TOKEN)

    async def notify(text, buttons=None):
        try:
            await bot.send_message(config.ADMIN_ID, text, buttons=buttons, link_preview=False)
        except Exception as e:
            print(f"[notify fail] {e}\n{text}")

    engine.bind(trader, notify)
    listener.register(user, trader, notify)
    control_bot.register(bot, trader)
    asyncio.create_task(engine.monitor_loop())
    asyncio.create_task(engine.agent_review_loop())
    asyncio.create_task(engine.daily_recap_loop())
    asyncio.create_task(engine.circuit_breaker_loop())

    await notify(control_bot.main_menu_text(), buttons=control_bot.main_menu_buttons())
    print(f"[hoodsniper v2] running. wallet={trader.addr}")
    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


if __name__ == "__main__":
    asyncio.run(main())
