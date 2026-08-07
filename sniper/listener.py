"""Listener userbot: pantau SEMUA source di DB (multi-group, dinamis tanpa restart)."""
import asyncio
import re
import traceback
from telethon import events, Button
from . import config, db, safety, agent, dexscreener as dx

CA_RE = re.compile(r"0x[a-fA-F0-9]{40}")

_trader = None
_notify = None


def topic_id(msg) -> int:
    r = msg.reply_to
    if r is not None and getattr(r, "forum_topic", False):
        return getattr(r, "reply_to_top_id", None) or r.reply_to_msg_id or 1
    return 1


def register(user_client, trader, notify):
    global _trader, _notify
    _trader, _notify = trader, notify

    @user_client.on(events.NewMessage())
    async def handler(event):
        try:
            src = db.match_source(event.chat_id, topic_id(event.message))
            if src is None:
                return
            await _handle(event, src)
        except Exception:
            traceback.print_exc()


async def _handle(event, src):
    if db.paused():
        return
    caller_id = event.sender_id
    caller_name = None
    try:
        sender = await event.get_sender()
        if sender:
            caller_name = getattr(sender, "username", None) or getattr(sender, "first_name", None)
    except Exception:
        pass
    if db.get("agent_blacklist", str) == "1" and db.is_blacklisted(caller_id):
        if CA_RE.search(event.raw_text or ""):
            print(f"[blacklist-skip] call dari {caller_name or caller_id} diabaikan (di-blacklist Mata)")
        return
    text = event.raw_text or ""
    for ca in dict.fromkeys(CA_RE.findall(text)):
        if db.seen_call(ca):
            continue
        db.record_call(ca, src["id"], caller_id, caller_name)

        pair = await asyncio.to_thread(dx.best_pair, ca)
        if not pair:
            print(f"[skip] {ca} tidak ketemu di chain '{config.DEX_CHAIN_MATCH}'")
            continue

        mcap = dx.mcap_of(pair)
        liq = dx.liq_usd(pair)
        sym = pair.get("baseToken", {}).get("symbol", "?")

        if mcap <= 0 or mcap > src["mcap_max"]:
            print(f"[skip] {sym} mcap ${mcap:,.0f} > limit ${src['mcap_max']:,.0f} ({src['name']})")
            continue
        if liq < db.get("min_liq_usd"):
            print(f"[skip] {sym} liq ${liq:,.0f} < min")
            continue

        ok, reason = await asyncio.to_thread(safety.check, _trader, ca, pair)
        if not ok:
            print(f"[safety-skip] {sym}: {reason}")
            await _notify(f"🛡 **SKIP {sym}** _via {src['name']}_\n`{ca}`\n{reason}")
            continue

        # ---------- 👁 MATA: keputusan entry oleh LLM (opsional) ----------
        decision = None
        if db.get("agent_on", str) == "1" and config.LLM_API_KEY:
            try:
                decision = await asyncio.to_thread(
                    agent.decide_entry, pair, ca, src, reason,
                    {"id": caller_id, "name": caller_name,
                     "track_record": db.caller_stats(caller_id)})
            except Exception as e:
                print(f"[agent fail, fallback rule-based] {sym}: {e}")
        if (decision and decision.get("blacklist_caller") and caller_id
                and db.get("agent_blacklist", str) == "1"):
            if db.get("agent_dry_run", str) == "1":
                await _notify(
                    f"🧪 **MODE LATIHAN — Mata MAU mem-blacklist {caller_name or caller_id}**\n"
                    f"_{decision['reason']}_\n(tidak dieksekusi karena masih latihan)")
            else:
                db.add_blacklist(caller_id, caller_name, decision["reason"])
                await _notify(
                    f"🚫 **MATA MEM-BLACKLIST caller {caller_name or caller_id}**\n"
                    f"_{decision['reason']}_\ncall dia berikutnya auto-skip. Kelola di panel 🧠 Otak.")
        if decision and decision["action"] == "SKIP":
            await _notify(
                f"👁 **MATA SKIP {sym}** _via {src['name']}_ (conviction {decision['conviction']})\n"
                f"`{ca}`\n_{decision['reason']}_")
            continue
        if decision and decision["action"] == "BUY" and decision["conviction"] < db.get("agent_min_conviction"):
            await _notify(
                f"👁 **MATA RAGU → SKIP {sym}** (conviction {decision['conviction']} < "
                f"min {db.get('agent_min_conviction'):.0f})\n_{decision['reason']}_")
            continue
        if decision and db.get("agent_dry_run", str) == "1":
            await _notify(
                f"🧪 **MODE LATIHAN — Mata mau BUY {sym}** (conviction {decision['conviction']})\n"
                f"size {decision['size_mult']:.2f}x · TP {decision['tp1']*100:.0f}%/{decision['tp2']*100:.0f}% · "
                f"SL {decision['sl']*100:.0f}%\n_{decision['reason']}_\n⚠️ risks: {decision['risks']}")
            continue

        usd = db.get("buy_usd") * decision["size_mult"] if decision else None
        try:
            pos = await asyncio.to_thread(_trader.buy, ca, pair, src["name"], usd)
        except Exception as e:
            await _notify(f"❌ Gagal buy **{sym}** dari _{src['name']}_: {e}")
            continue

        db.open_position(**pos)
        pid = max(p["id"] for p in db.open_positions() if p["ca"] == pos["ca"])
        agent_line = ""
        if decision:
            db.update_position(pid, tp1_x=decision["tp1"], tp2_x=decision["tp2"], sl_x=decision["sl"])
            agent_line = (f"\n👁 conviction {decision['conviction']} · size {decision['size_mult']:.2f}x · "
                          f"TP {decision['tp1']*100:.0f}/{decision['tp2']*100:.0f} · SL {decision['sl']*100:.0f}\n"
                          f"_{decision['reason']}_")
        if caller_id:
            db.update_position(pid, caller_id=caller_id, caller_name=caller_name)
        cs = db.caller_stats(caller_id)
        caller_line = ""
        if caller_name and cs and cs["calls_bought"] > 1:
            caller_line = (f"\n🎯 caller: {caller_name} — {cs['calls_bought']} call, "
                           f"{cs['hit_tp1'] or 0} TP1, {cs['stopped'] or 0} SL")
        elif caller_name:
            caller_line = f"\n🎯 caller: {caller_name} (baru)"
        hp = "\n⚠️ **SIMULASI SELL GAGAL — kemungkinan HONEYPOT!**" if pos["honeypot"] else ""
        chart = f"https://dexscreener.com/{pos['chain_slug']}/{pos['pair_address']}"
        txurl = f"{config.EXPLORER_URL}/tx/{pos['buy_tx']}" if pos.get("buy_tx") else None
        await _notify(
            f"🟢 **BUY {pos['symbol']}**  _via {src['name']}_\n"
            f"`{pos['ca']}`\n"
            f"💰 mcap ${mcap:,.0f} | 💧 liq ${liq:,.0f} | {pos['pool_type']}\n"
            f"masuk **${pos['usd_spent']:.2f}** ({pos['eth_spent']:.6f} ETH) → {pos['tokens_total']:,.0f} {pos['symbol']}\n"
            f"entry ${pos['entry_price_usd']:.10f}{caller_line}{hp}{agent_line}",
            buttons=[
                [Button.inline("📉 Sell 50%", f"sell:{pid}:50"),
                 Button.inline("🚨 Sell ALL", f"sell:{pid}:100")],
                [Button.url("📊 Chart", chart)] + ([Button.url("🔍 Tx", txurl)] if txurl else []),
            ],
        )
