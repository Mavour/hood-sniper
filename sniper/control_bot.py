"""Control panel via bot BotFather — gaya trading bot (inline keyboard).

Menu: Posisi (PnL live + tombol sell), Sources (multi-group add/toggle/hapus),
Settings (edit semua parameter), Stats, Pause/Resume.
"""
import asyncio
import re
import traceback
from telethon import events, Button
from . import config, db, engine, agent, dexscreener as dx

_pending = {}  # admin_id -> action string ("add_source" | "set:key")
_trader = None

LINK_RE = re.compile(r"t\.me/c/(\d+)(?:/(\d+))?")


def _admin(event):
    return event.sender_id == config.ADMIN_ID


# ---------- panel builders ----------
def main_menu_text():
    bal = _trader.eth_balance()
    if db.paused() and engine._cb_tripped:
        st = "🔴 CIRCUIT BREAKER — entry dipause otomatis"
    else:
        st = "⏸ PAUSED" if db.paused() else "▶️ AKTIF"
    n_open = len(db.open_positions())
    n_src = len(db.sources(only_enabled=True))
    return (
        f"🤖 **hoodsniper v2**\n\n"
        f"status: **{st}**\n"
        f"👛 `{_trader.addr}`\n"
        f"⛽ saldo: **{bal:.6f} ETH**\n"
        f"📊 posisi open: **{n_open}** | 📡 sources aktif: **{n_src}**\n"
        f"💵 buy size: **${db.get('buy_usd'):.2f}**/call"
    )


def main_menu_buttons():
    pause_btn = Button.inline("▶️ Resume", b"pause") if db.paused() else Button.inline("⏸ Pause", b"pause")
    return [
        [Button.inline("📊 Posisi", b"positions"), Button.inline("📡 Sources", b"sources")],
        [Button.inline("⚙️ Settings", b"settings"), Button.inline("📈 Stats", b"stats")],
        [Button.inline("🧠 Otak", b"agent"), pause_btn],
        [Button.inline("🔄 Refresh", b"menu")],
    ]


async def positions_panel():
    rows = db.open_positions()
    if not rows:
        return "📊 **Posisi**\n\nTidak ada posisi terbuka.", [[Button.inline("« Menu", b"menu")]]
    lines, buttons = ["📊 **Posisi Terbuka**\n"], []
    for p in rows:
        pair = await engine.current_pair(p)
        price = float((pair or {}).get("priceUsd") or 0)
        pnl = (price / p["entry_price_usd"] - 1) * 100 if price > 0 else 0
        emo = "🟢" if pnl >= 0 else "🔴"
        tp1 = " · TP1✓" if p["tp1_done"] else ""
        lines.append(
            f"{emo} **{p['symbol']}** {pnl:+.1f}%{tp1} _via {p['source_name']}_\n"
            f"   entry ${p['entry_price_usd']:.10f} | sisa {p['tokens_left']:,.0f}"
        )
        buttons.append([
            Button.inline(f"{p['symbol']} 📉50%", f"sell:{p['id']}:50"),
            Button.inline("🚨 ALL", f"sell:{p['id']}:100"),
            Button.url("📊", f"https://dexscreener.com/{p['chain_slug']}/{p['pair_address']}"),
        ])
    buttons.append([Button.inline("« Menu", b"menu")])
    return "\n".join(lines), buttons


def sources_panel():
    rows = db.sources()
    lines, buttons = ["📡 **Sources (mirror via akun sendiri)**\n"], []
    for s in rows:
        st = "✅" if s["enabled"] else "🚫"
        tp = f" · topic {s['topic_id']}" if s["topic_id"] else " · semua topic"
        lines.append(f"{st} **{s['name']}** — mcap ≤ ${s['mcap_max']:,.0f}\n   `{s['chat_id']}`{tp}")
        buttons.append([
            Button.inline(f"{'🚫 Off' if s['enabled'] else '✅ On'} {s['name'][:14]}", f"srct:{s['id']}"),
            Button.inline("🗑", f"srcd:{s['id']}"),
        ])
    buttons.append([Button.inline("➕ Tambah Source", b"srcadd"), Button.inline("« Menu", b"menu")])
    return "\n".join(lines), buttons


def settings_panel():
    lines, buttons = ["⚙️ **Settings** (tap untuk edit)\n"], []
    row = []
    for key, label in config.SETTING_LABELS.items():
        lines.append(f"{label}: **{db.get(key, str)}**")
        row.append(Button.inline(label.split(" ")[0] + " " + key, f"set:{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([Button.inline("« Menu", b"menu")])
    return "\n".join(lines), buttons


def stats_panel():
    s = db.stats()
    pnl_eth = s["eth_out"] - s["eth_in"]
    lines = [
        "📈 **Stats**\n",
        f"📨 call terdeteksi: **{s['calls']}**",
        f"🟢 total buy: **{s['buys']}** (${s['spent']:.2f})",
        f"📊 open: **{s['open']}** | 🎯 TP2: **{s['tp2']}** | 📈 trail: **{s['trail']}** | 🛑 SL: **{s['sl']}** | 🚨 rug: **{s['rug']}** | 👆 manual: **{s['manual']}** | 🧹 empty: **{s['empty']}**",
        f"⛽ ETH keluar (incl. gas buy): {s['eth_in']:.6f} | ETH balik (real): {s['eth_out']:.6f}",
        f"💰 PnL realized: **{pnl_eth:+.6f} ETH**",
    ]
    top = db.top_callers(5)
    if top:
        lines.append("\n🏆 **Skor Caller** (TP1-rate)")
        for i, cl in enumerate(top, 1):
            rate = (cl["hit_tp1"] or 0) / cl["buys"] * 100 if cl["buys"] else 0
            lines.append(f"{i}. **{cl['caller_name'] or '?'}** — {cl['buys']} call · "
                         f"{rate:.0f}% TP1 · {cl['full_tp'] or 0} full TP · {cl['stopped'] or 0} SL")
    return "\n".join(lines), [[Button.inline("« Menu", b"menu")]]


def agent_panel():
    on = db.get("agent_on", str) == "1"
    manage = db.get("agent_manage_on", str) == "1"
    dry = db.get("agent_dry_run", str) == "1"
    key_ok = bool(config.LLM_API_KEY)
    n_lessons = len(db.recent_lessons(100))
    lines = [
        "🧠 **OTAK — lapisan LLM hoodsniper**\n",
        f"provider: **{config.LLM_PROTOCOL}**" + (f" · `{config.LLM_BASE_URL}`" if config.LLM_BASE_URL else ""),
        f"API key: {'✅ terpasang' if key_ok else '❌ belum ada LLM_API_KEY di .env'}",
        f"👁 Mata (keputusan entry): **{'ON' if on else 'OFF'}**",
        f"✋ Tangan (review posisi): **{'ON' if manage else 'OFF'}** (tiap {db.get('agent_review_min'):.0f} mnt)",
        f"mode: **{'🧪 MODE LATIHAN' if dry else '🔴 LIVE'}**",
        f"🚫 blacklist caller: **{'ON' if db.get('agent_blacklist', str) == '1' else 'OFF'}** "
        f"({len(db.list_blacklist())} caller terdaftar)",
        f"model: `{db.get('agent_model', str)}`",
        f"💡 insting tersimpan: **{n_lessons}**\n",
    ]
    for d in db.recent_decisions(3):
        oc = f" → {d['outcome']}" if d.get("outcome") else ""
        lines.append(f"• [{d['role']}] **{d['action']} {d['symbol']}** ({d['conviction']}){oc}\n  _{d['reason'][:80]}_")
    buttons = [
        [Button.inline(f"{'🔴 Off' if on else '🟢 On'} Mata", b"agt:on"),
         Button.inline(f"{'🔴 Off' if manage else '🟢 On'} Tangan", b"agt:manage")],
        [Button.inline(f"{'🔴 Live-kan' if dry else '🧪 Mode Latihan'}", b"agt:dry"),
         Button.inline("🤖 Ganti model", b"agt:model")],
        [Button.inline("👣 Jejak", b"agt:log"),
         Button.inline("💡 Latih insting", b"agt:lessons")],
        [Button.inline("🎓 Coach sekarang", b"agt:coach"),
         Button.inline("🚫 Blacklist", b"agt:bl")],
        [Button.inline(f"{'🔴 Off' if db.get('agent_blacklist', str) == '1' else '🟢 On'} blacklist", b"agt:blt")],
        [Button.inline("« Menu", b"menu")],
    ]
    return "\n".join(lines), buttons


# ---------- register handlers ----------
def register(bot, trader):
    global _trader
    _trader = trader

    @bot.on(events.NewMessage(pattern=r"^/start|^/menu"))
    async def start(event):
        if not _admin(event):
            return await event.respond("⛔ Bot privat.")
        _pending.pop(event.sender_id, None)
        await event.respond(main_menu_text(), buttons=main_menu_buttons())

    @bot.on(events.NewMessage(pattern=r"^/tanya\s+(.+)", func=lambda e: e.is_private))
    async def tanya_cmd(event):
        if not _admin(event):
            return
        if not config.LLM_API_KEY:
            return await event.respond("LLM_API_KEY belum diisi — /tanya butuh Otak nyala.")
        q = event.pattern_match.group(1)
        await event.respond("🧠 mikir…")
        from . import agent as _agent, engine as _engine
        import time as _t
        ctx = {
            "stats": db.stats(),
            "open_positions": [{k: p.get(k) for k in
                ("symbol", "entry_price_usd", "tokens_left", "tp1_done", "source_name", "caller_name")}
                for p in db.open_positions()],
            "recent_decisions": db.recent_decisions(8),
            "top_callers": db.top_callers(5),
            "blacklist": db.list_blacklist(),
            "settings": {k: db.get(k, str) for k in _agent.COACH_KEYS},
            "recap_24h": _engine.build_recap(int(_t.time()) - 86400),
        }
        try:
            ans = await __import__("asyncio").to_thread(_agent.tanya, q, ctx)
            await event.respond(ans or "…kosong")
        except Exception as e:
            await event.respond(f"⚠️ gagal: {e}")

    @bot.on(events.CallbackQuery())
    async def cb(event):
        if not _admin(event):
            return await event.answer("⛔", alert=True)
        data = event.data.decode()
        try:
            await _route(event, data)
        except Exception as e:
            traceback.print_exc()
            await event.answer(f"error: {e}"[:190], alert=True)

    @bot.on(events.NewMessage())
    async def text_input(event):
        if not _admin(event) or event.raw_text.startswith("/"):
            return
        action = _pending.pop(event.sender_id, None)
        if not action:
            return
        if action == "add_source":
            await _do_add_source(event)
        elif action == "agentset:agent_model":
            db.set_("agent_model", event.raw_text.strip())
            await event.respond(f"✅ model → `{event.raw_text.strip()}`")
            t, b = agent_panel()
            await event.respond(t, buttons=b)
        elif action.startswith("set:"):
            key = action[4:]
            val = event.raw_text.strip()
            try:
                float(val)
                db.set_(key, val)
                await event.respond(f"✅ **{config.SETTING_LABELS[key]}** → `{val}`")
            except ValueError:
                await event.respond("❌ Harus angka. Ulangi dari ⚙️ Settings.")
            t, b = settings_panel()
            await event.respond(t, buttons=b)


async def _route(event, data):
    if data == "menu":
        await event.edit(main_menu_text(), buttons=main_menu_buttons())
    elif data == "positions":
        await event.answer("loading harga…")
        t, b = await positions_panel()
        await event.edit(t, buttons=b)
    elif data == "sources":
        t, b = sources_panel()
        await event.edit(t, buttons=b)
    elif data == "settings":
        t, b = settings_panel()
        await event.edit(t, buttons=b)
    elif data == "stats":
        t, b = stats_panel()
        await event.edit(t, buttons=b)
    elif data == "agent":
        t, b = agent_panel()
        await event.edit(t, buttons=b)
    elif data == "agt:on":
        db.set_("agent_on", "0" if db.get("agent_on", str) == "1" else "1")
        t, b = agent_panel(); await event.edit(t, buttons=b)
    elif data == "agt:manage":
        db.set_("agent_manage_on", "0" if db.get("agent_manage_on", str) == "1" else "1")
        t, b = agent_panel(); await event.edit(t, buttons=b)
    elif data == "agt:dry":
        db.set_("agent_dry_run", "0" if db.get("agent_dry_run", str) == "1" else "1")
        if db.get("agent_dry_run", str) == "0":
            await event.answer("🔴 LIVE — Otak sekarang eksekusi dana beneran", alert=True)
        t, b = agent_panel(); await event.edit(t, buttons=b)
    elif data == "agt:model":
        _pending[event.sender_id] = "agentset:agent_model"
        await event.respond(f"🤖 Model sekarang: `{db.get('agent_model', str)}`\nKirim nama model baru:")
    elif data == "agt:log":
        rows = db.recent_decisions(8)
        if not rows:
            await event.answer("belum ada keputusan", alert=True)
        else:
            lines = ["👣 **JEJAK — riwayat keputusan Otak**\n"]
            for d in rows:
                oc = f" → {d['outcome']}" if d.get("outcome") else ""
                lines.append(f"• [{d['role']}] **{d['action']} {d['symbol']}** ({d['conviction']}){oc}\n  _{d['reason'][:100]}_")
            await event.respond("\n".join(lines))
    elif data == "agt:lessons":
        await event.answer("melatih insting dari posisi closed…")
        try:
            out = await __import__("asyncio").to_thread(agent.derive_lessons)
            if out:
                await event.respond("💡 **Insting baru terbentuk:**\n" + "\n".join(f"• {t}" for t in out))
            else:
                await event.respond("Belum cukup pengalaman — butuh ≥5 posisi closed buat melatih insting.")
        except Exception as e:
            await event.respond(f"⚠️ gagal melatih insting: {e}")
    elif data == "agt:coach":
        await event.answer("coach menganalisa 24 jam terakhir…")
        try:
            await engine.run_coach()
        except Exception as e:
            await event.respond(f"⚠️ coach gagal: {e}")
    elif data == "agt:bl":
        rows = db.list_blacklist()
        if not rows:
            await event.respond("🚫 Blacklist kosong — Mata belum mem-blacklist caller siapapun.")
        else:
            lines = ["🚫 **Caller yang di-blacklist Mata** (call mereka auto-skip):\n"]
            btns = []
            for r in rows:
                lines.append(f"• **{r['caller_name'] or r['caller_id']}**\n  _{(r['reason'] or '')[:90]}_")
                btns.append([Button.inline(f"♻️ Pulihkan {str(r['caller_name'] or r['caller_id'])[:16]}",
                                           f"blrm:{r['caller_id']}")])
            btns.append([Button.inline("« Menu", b"menu")])
            await event.respond("\n".join(lines), buttons=btns)
    elif data == "agt:blt":
        db.set_("agent_blacklist", "0" if db.get("agent_blacklist", str) == "1" else "1")
        on = db.get("agent_blacklist", str) == "1"
        await event.answer("🚫 blacklist AKTIF" if on else "blacklist NONAKTIF — daftar tetap tersimpan")
        t, b = agent_panel(); await event.edit(t, buttons=b)
    elif data.startswith("blrm:"):
        db.remove_blacklist(int(data[5:]))
        await event.answer("♻️ dipulihkan — call dia bisa masuk lagi")
    elif data.startswith("applyset:"):
        _, key, val = data.split(":", 2)
        from . import agent as _agent
        if key not in _agent.COACH_KEYS:
            return await event.answer("⛔ key tidak diizinkan", alert=True)
        lo, hi = _agent.COACH_KEYS[key]
        try:
            v = max(lo, min(hi, float(val)))
        except ValueError:
            return await event.answer("⛔ nilai tidak valid", alert=True)
        db.set_(key, v)
        await event.answer(f"✅ {key} → {v}")
        await event.respond(f"🎓 Diterapkan: **{key} = {v}**")
    elif data == "pause":
        db.set_("paused", "0" if db.paused() else "1")
        await event.answer("⏸ paused" if db.paused() else "▶️ resumed")
        await event.edit(main_menu_text(), buttons=main_menu_buttons())
    elif data.startswith("sell:"):
        _, pid, pct = data.split(":")
        await event.answer("eksekusi sell…")
        result = await engine.execute_sell(int(pid), int(pct) / 100, "manual")
        await event.respond(result)
    elif data.startswith("srct:"):
        db.toggle_source(int(data[5:]))
        t, b = sources_panel()
        await event.edit(t, buttons=b)
    elif data.startswith("srcd:"):
        db.del_source(int(data[5:]))
        await event.answer("🗑 dihapus")
        t, b = sources_panel()
        await event.edit(t, buttons=b)
    elif data == "srcadd":
        _pending[event.sender_id] = "add_source"
        await event.respond(
            "➕ **Tambah source** — kirim 1 baris:\n\n"
            "`<link_topic_atau_chat_id> <mcap_max> <nama>`\n\n"
            "contoh:\n"
            "`https://t.me/c/2103131992/250551 30000 Degen Hood`\n"
            "`-1001234567890 15000 Alpha Group` _(tanpa topic = semua message)_\n\n"
            "⚠️ akun Telegram Kakak harus sudah jadi member group-nya."
        )
    elif data.startswith("set:"):
        key = data[4:]
        _pending[event.sender_id] = data
        await event.respond(
            f"⚙️ **{config.SETTING_LABELS[key]}**\n"
            f"nilai sekarang: `{db.get(key, str)}`\n\nKirim nilai baru (angka):"
        )


async def _do_add_source(event):
    parts = event.raw_text.strip().split(maxsplit=2)
    if len(parts) < 3:
        return await event.respond("❌ Format: `<link/chat_id> <mcap_max> <nama>`")
    target, mcap_s, name = parts
    m = LINK_RE.search(target)
    if m:
        chat_id = int(f"-100{m.group(1)}")
        topic = int(m.group(2) or 0)
    else:
        try:
            chat_id = int(target)
            topic = 0
        except ValueError:
            return await event.respond("❌ Link/chat_id tidak valid.")
    try:
        mcap = float(mcap_s)
    except ValueError:
        return await event.respond("❌ mcap_max harus angka.")
    db.add_source(chat_id, topic, name, mcap)
    await event.respond(f"✅ Source **{name}** ditambahkan — `{chat_id}`"
                        f"{f' topic {topic}' if topic else ''} | mcap ≤ ${mcap:,.0f}")
    t, b = sources_panel()
    await event.respond(t, buttons=b)
