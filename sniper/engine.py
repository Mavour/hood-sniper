"""Engine jual (dipakai monitor TP/SL & tombol manual di control bot) + loop monitor."""
import asyncio
import traceback
from . import config, db, dexscreener as dx


def _txlink(h):
    return f"[🔍 tx]({config.EXPLORER_URL}/tx/{h})" if h else ""

_trader = None
_notify = None  # async fn(text, buttons=None, **kw)
_thin_warned = set()  # pos_id yang sudah dikasih warning pool tipis (anti-spam)
_liq_last = {}       # pos_id -> liq USD terakhir (rug monitor)


def bind(trader, notify):
    global _trader, _notify
    _trader, _notify = trader, notify


async def current_pair(pos):
    return await asyncio.to_thread(dx.get_pair, pos["chain_slug"], pos["pair_address"])


async def execute_sell(pos_id: int, fraction: float, reason: str):
    """Jual fraction dari sisa posisi. reason: tp1|tp2|sl|manual.

    PnL dilaporkan DUA-DUANYA:
    - layar  : dari harga spot Dexscreener (yang men-trigger TP/SL)
    - realized: dari ETH ASLI yang masuk wallet vs porsi ETH keluar saat beli
    Kalau layar jauh di atas realized → peringatan pool tipis (price impact).
    """
    pos = db.position(pos_id)
    if not pos or pos["status"] != "open":
        return "posisi sudah closed / tidak ditemukan"
    pair = await current_pair(pos)
    if not pair:
        return "gagal ambil harga pair dari Dexscreener"
    price = float(pair.get("priceUsd") or 0)
    price_native = float(pair.get("priceNative") or 0)
    spot_pnl = price / pos["entry_price_usd"] - 1 if price > 0 else 0

    sold, eth_real, gas_eth, swap_tx = await asyncio.to_thread(_trader.sell, pos, fraction, price_native)
    if sold <= 0:
        import time as _t
        db.update_position(pos_id, tokens_left=0, status="closed_empty", closed_at=int(_t.time()))
        _cleanup(pos_id)
        return f"{pos['symbol']}: balance 0 — tidak ada yang bisa dijual, posisi DITUTUP (closed_empty)"

    db.add_realized(pos_id, eth_real)

    # porsi modal ETH untuk token yang barusan dijual
    cost_eth = pos["eth_spent"] * (sold / pos["tokens_total"]) if pos["tokens_total"] else 0
    real_pnl = (eth_real / cost_eth - 1) if cost_eth > 0 else 0
    real_pnl = max(real_pnl, -1.0)

    thin = spot_pnl > 0 and (spot_pnl - real_pnl) > 0.30
    # Full exit (fraction~1): sisa 0. Partial: kurangi DB, jangan negatif.
    if fraction >= 0.999:
        left = 0.0
    else:
        left = max(float(pos["tokens_left"] or 0) - sold, 0.0)

    tag = f"{pos['symbol']} (`{pos['ca'][:10]}…`)"
    nums = (f"layar {spot_pnl*100:+.1f}% → **realized {real_pnl*100:+.1f}%**\n"
            f"{sold:,.0f} token → **{eth_real:.6f} ETH** masuk · gas {gas_eth:.6f} {_txlink(swap_tx)}")
    warn = "\n⚠️ _pool tipis: harga layar jauh di atas hasil jual asli (price impact)_" if thin else ""

    import time as _t
    now = int(_t.time())
    is_close = reason in ("tp2", "sl", "rug", "trail") or (reason == "manual" and (fraction >= 1 or left <= 0))
    if is_close and db.get("agent_auto_insting", str) == "1" and config.LLM_API_KEY:
        st = db.stats()
        total_closed = st["tp2"] + st["sl"] + st["manual"] + st["rug"] + st["trail"] + 1
        if total_closed >= 5 and total_closed % 5 == 0:
            asyncio.create_task(_auto_insting())
    if reason in ("tp2", "sl", "rug", "trail") or (reason == "manual" and (fraction >= 1 or left <= 0)):
        db.mark_decision_outcome(pos["ca"], f"{reason} real {real_pnl*100:+.0f}% (layar {spot_pnl*100:+.0f}%)")

    if reason == "tp1":
        if left <= 0:
            db.update_position(pos_id, tokens_left=0, tp1_done=1, status="closed_tp1", closed_at=now)
            text = f"✅ **TP1 — full exit** {tag}\n{nums}\n(tp1_sell_pct menjual semua) posisi CLOSED{warn}"
        else:
            db.update_position(pos_id, tokens_left=left, tp1_done=1)
            text = f"✅ **TP1** {tag}\n{nums}\nhold moonbag {left:,.0f}{warn}"
    elif reason == "tp2":
        db.update_position(pos_id, tokens_left=0, status="closed_tp2", closed_at=now)
        text = f"🎯 **TP2** {tag}\n{nums}\nposisi CLOSED{warn}"
    elif reason == "sl":
        db.update_position(pos_id, tokens_left=0, status="closed_sl", closed_at=now)
        text = f"🛑 **STOP LOSS** {tag}\n{nums}\nposisi CLOSED{warn}"
    elif reason == "rug":
        db.update_position(pos_id, tokens_left=0, status="closed_rug", closed_at=now)
        text = f"🚨 **RUG ALERT — liquidity ditarik!** {tag}\nemergency exit sebelum pintu ketutup:\n{nums}\nposisi CLOSED{warn}"
    elif reason == "trail":
        db.update_position(pos_id, tokens_left=0, status="closed_trail", closed_at=now)
        text = f"📈🔒 **TRAILING STOP** {tag}\nprofit diamankan dari puncak:\n{nums}\nposisi CLOSED{warn}"
    else:
        if fraction >= 1 or left <= 0:
            db.update_position(pos_id, tokens_left=0, status="closed_manual", closed_at=now)
            text = f"👆 **Manual sell ALL** {tag}\n{nums}\nposisi CLOSED{warn}"
        else:
            db.update_position(pos_id, tokens_left=left)
            text = f"👆 **Manual sell {fraction*100:.0f}%** {tag}\n{nums}\nsisa {left:,.0f}{warn}"
    return text


async def monitor_loop():
    while True:
        try:
            for pos in db.open_positions():
                await _check(pos)
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(int(db.get("poll_seconds")))


async def _check(pos):
    # pembersih zombie: open tapi sisa 0 (dari versi lama / dust) → tutup
    if (pos["tokens_left"] or 0) <= 0:
        import time as _t
        db.update_position(pos["id"], status="closed_empty", closed_at=int(_t.time()))
        _cleanup(pos["id"])
        await _notify(f"🧹 **{pos['symbol']}** sisa 0 tapi status masih open — ditutup otomatis (pembersihan)")
        return
    pair = await current_pair(pos)
    if not pair:
        return
    price = float(pair.get("priceUsd") or 0)
    if price <= 0:
        return
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)

    # ---------- 1) RUG MONITOR: liq drop mendadak → emergency exit ----------
    # Abaikan pembacaan liq 0/None (Dexscreener stale) — jangan update baseline
    # dan jangan trigger, biar satu data buruk nggak bikin false rug.
    last_liq = _liq_last.get(pos["id"])
    if liq > 0:
        _liq_last[pos["id"]] = liq
    if last_liq and last_liq > 0 and liq > 0 and liq < last_liq * (1 - db.get("rug_liq_drop")):
        try:
            _liq_last.pop(pos["id"], None)
            _thin_warned.discard(pos["id"])
            await _notify(
                f"🚨 **{pos['symbol']}** liq anjlok ${last_liq:,.0f} → ${liq:,.0f} "
                f"({(liq/last_liq-1)*100:.0f}%) — EMERGENCY EXIT!")
            await _notify(await execute_sell(pos["id"], 1.0, "rug"))
        except Exception as e:
            await _notify(f"⚠️ Emergency exit {pos['symbol']} GAGAL (LP mungkin sudah kosong): {e}")
        return

    pnl = price / pos["entry_price_usd"] - 1
    tp1_v = pos.get("tp1_x") or db.get("tp1")
    tp2_v = pos.get("tp2_x") or db.get("tp2")
    sl_v = pos.get("sl_x") or db.get("sl")

    # ---------- 2) peak tracking (buat trailing) ----------
    peak = pos.get("peak_price") or pos["entry_price_usd"]
    if price > peak:
        peak = price
        db.update_position(pos["id"], peak_price=peak)

    try:
        # ---------- 3) sebelum TP1: SL normal dari entry ----------
        if not pos["tp1_done"] and pnl <= sl_v:
            _cleanup(pos["id"])
            await _notify(await execute_sell(pos["id"], 1.0, "sl"))
            return

        # ---------- 4) setelah TP1: breakeven lock + trailing stop ----------
        if pos["tp1_done"] and db.get("trail_on", str) == "1":
            stop_level = max(pos["entry_price_usd"], peak * (1 - db.get("trail_pct")))
            if price <= stop_level:
                _cleanup(pos["id"])
                await _notify(await execute_sell(pos["id"], 1.0, "trail"))
                return
        elif pos["tp1_done"] and pnl <= sl_v:
            _cleanup(pos["id"])
            await _notify(await execute_sell(pos["id"], 1.0, "sl"))
            return

        verify = db.get("tp_verify", str) == "1"
        if pnl >= tp2_v and db.get("tp2_on", str) == "1":
            if verify:
                ep = await asyncio.to_thread(_trader.exec_pnl, pos, 1.0)
                # ep < 0 = quote gagal (dulu sering muncur sebagai 'STF' palsu)
                if ep < tp2_v * 0.7:
                    if pos["id"] not in _thin_warned:
                        _thin_warned.add(pos["id"])
                        if ep < 0:
                            await _notify(
                                f"🫧 **{pos['symbol']}** layar {pnl*100:+.0f}% tapi quote jual "
                                f"gagal/pool tidak cukup depth — TP2 ditahan, coba lagi")
                        else:
                            await _notify(
                                f"🫧 **{pos['symbol']}** layar {pnl*100:+.0f}% tapi hasil jual asli "
                                f"cuma {ep*100:+.0f}% (pool tipis) — TP2 ditahan, tunggu depth")
                    return
            _cleanup(pos["id"])
            await _notify(await execute_sell(pos["id"], 1.0, "tp2"))
        elif pnl >= tp1_v and not pos["tp1_done"]:
            frac = db.get("tp1_sell_pct")
            if verify:
                ep = await asyncio.to_thread(_trader.exec_pnl, pos, frac)
                if ep < tp1_v * 0.7:
                    if pos["id"] not in _thin_warned:
                        _thin_warned.add(pos["id"])
                        if ep < 0:
                            await _notify(
                                f"🫧 **{pos['symbol']}** layar {pnl*100:+.0f}% tapi quote jual "
                                f"gagal/pool tidak cukup depth — TP1 ditahan, coba lagi")
                        else:
                            await _notify(
                                f"🫧 **{pos['symbol']}** layar {pnl*100:+.0f}% tapi hasil jual asli "
                                f"cuma {ep*100:+.0f}% (pool tipis) — TP1 ditahan, tunggu depth")
                    return
            _thin_warned.discard(pos["id"])
            await _notify(await execute_sell(pos["id"], frac, "tp1"))
    except Exception as e:
        err = str(e)
        hint = ""
        if "STF" in err:
            hint = ("\n_STF = transferFrom gagal (saldo/allowance/amount). "
                    "Biasanya float amount > balance atau race sell ganda._")
        await _notify(f"⚠️ Gagal eksekusi sell {pos['symbol']}: {e}{hint}")


def _cleanup(pid):
    _thin_warned.discard(pid)
    _liq_last.pop(pid, None)


# ---------- 🔴 CIRCUIT BREAKER: auto-pause entry kalau rugi tembus limit ----------
_cb_tripped = False  # anti-spam: sekali notify per breach, sampai net pulih


async def circuit_breaker_loop():
    """Cek tiap 60 detik. Kalau realized PnL dalam window terakhir <= -limit,
    entry di-pause otomatis (posisi open TETAP dimonitor, TP/SL/trail jalan normal).
    Perlu di-unpause manual dari menu setelah Kakak review — breaker tidak
    auto-resume, biar tidak langsung entry lagi sambil kondisi masih sama."""
    global _cb_tripped
    import time as _t
    while True:
        await asyncio.sleep(60)
        try:
            if db.get("circuit_breaker_on", str) != "1":
                _cb_tripped = False
                continue
            limit = db.get("daily_loss_limit_eth")
            if limit <= 0:
                continue
            window_min = int(db.get("circuit_breaker_window_min"))
            since = int(_t.time()) - window_min * 60
            net = db.realized_pnl_window(since)
            if net <= -abs(limit):
                if not _cb_tripped:
                    _cb_tripped = True
                    db.set_("paused", "1")
                    await _notify(
                        f"🔴 **CIRCUIT BREAKER TRIPPED**\n"
                        f"Realized PnL {net:+.6f} ETH dalam {window_min} menit terakhir "
                        f"(limit -{abs(limit):.6f} ETH).\n"
                        f"⏸ Entry baru **DIPAUSE OTOMATIS**. Posisi yang sudah open tetap "
                        f"dimonitor normal (TP/SL/trail jalan).\n"
                        f"Review dulu sebelum ▶️ Resume manual — breaker tidak auto-resume.")
            elif net > -abs(limit) * 0.5:
                # net sudah pulih signifikan → reset flag biar bisa trip lagi kalau jatuh lagi nanti
                _cb_tripped = False
        except Exception:
            traceback.print_exc()


async def agent_review_loop():
    """Review posisi terbuka oleh agent (role manager). TP/SL deterministik tetap backstop."""
    from . import agent
    while True:
        await asyncio.sleep(max(int(db.get("agent_review_min")), 2) * 60)
        if db.get("agent_manage_on", str) != "1" or not config.LLM_API_KEY:
            continue
        for pos in db.open_positions():
            try:
                pair = await current_pair(pos)
                if not pair:
                    continue
                action, reason = await asyncio.to_thread(agent.review_position, pos, pair)
                if action == "SELL_HALF":
                    result = await execute_sell(pos["id"], 0.5, "manual")
                    await _notify(f"✋ **TANGAN exit 50%** {pos['symbol']}\n_{reason}_\n{result}")
                elif action == "SELL_ALL":
                    result = await execute_sell(pos["id"], 1.0, "manual")
                    await _notify(f"✋ **TANGAN exit ALL** {pos['symbol']}\n_{reason}_\n{result}")
            except Exception as e:
                print(f"[agent-review fail] {pos['symbol']}: {e}")


async def daily_recap_loop():
    """Rekap harian jam recap_hour WIB (UTC+7)."""
    import time as _t
    while True:
        now = _t.time()
        wib = now + 7 * 3600
        target_hour = int(db.get("recap_hour"))
        secs_today = wib % 86400
        target_secs = target_hour * 3600
        wait = (target_secs - secs_today) % 86400
        if wait < 60:
            wait += 86400
        await asyncio.sleep(wait)
        if db.get("recap_on", str) != "1":
            continue
        try:
            await _notify(build_recap(int(_t.time()) - 86400))
            if db.get("agent_coach", str) == "1" and config.LLM_API_KEY:
                await run_coach()
        except Exception:
            traceback.print_exc()


def build_recap(since_ts: int) -> str:
    d = db.recap_data(since_ts)
    closed = {c["status"]: c for c in d["closed"]}
    n = lambda k: closed.get(k, {}).get("n", 0)
    eth_in = sum(c["eth_in"] for c in d["closed"])
    eth_out = sum(c["eth_out"] for c in d["closed"])
    net = eth_in - eth_out
    emo = "🟢" if net >= 0 else "🔴"
    skipped = max(d["calls"] - d["buys"], 0)
    lines = [
        "🌅 **REKAP 24 JAM — hoodsniper**\n",
        f"📨 call masuk: **{d['calls']}** · di-skip filter: {skipped}",
        f"🛒 entry baru: **{d['buys']}** (${d['usd_spent']:.2f})",
        f"🎯 TP2: {n('closed_tp2')} · ✅→📈 trail: {n('closed_trail')} · "
        f"🛑 SL: {n('closed_sl')} · 🚨 rug: {n('closed_rug')} · 👆 manual: {n('closed_manual')}",
        f"{emo} PnL realized (posisi closed): **{net:+.6f} ETH**",
        f"📊 masih open: {d['open']} posisi",
    ]
    if d["top_caller"]:
        t = d["top_caller"]
        lines.append(f"🏆 caller terbaik: **{t['caller_name']}** ({t['tp1'] or 0} TP1 dari {t['n']} call)")
    lines.append("\n_semua angka = realized asli dari wallet, bukan estimasi layar_")
    return "\n".join(lines)


async def _auto_insting():
    """Latih insting otomatis tiap 5 posisi closed."""
    from . import agent
    try:
        out = await asyncio.to_thread(agent.derive_lessons)
        if out:
            await _notify("💡 **Insting baru (otomatis, dari 5 close terakhir):**\n" +
                          "\n".join(f"• {t}" for t in out))
    except Exception as e:
        print(f"[auto-insting fail] {e}")


async def run_coach():
    """🎓 Coach: analisa 24 jam + usulan setting (butuh persetujuan via tombol)."""
    import time as _t
    from telethon import Button
    from . import agent, config as cfg
    recap = build_recap(int(_t.time()) - 86400)
    settings = {k: db.get(k, str) for k in agent.COACH_KEYS}
    props = await asyncio.to_thread(agent.coach, recap, settings)
    if not props:
        await _notify("🎓 **Coach:** setting sekarang sudah pas menurut data 24 jam terakhir — tidak ada usulan.")
        return
    lines = ["🎓 **COACH — usulan penyesuaian** (tap ✅ untuk terapkan):\n"]
    buttons = []
    for p in props:
        cur = db.get(p["key"], str)
        lines.append(f"• `{p['key']}`: {cur} → **{p['value']}**\n  _{p['why']}_")
        buttons.append([Button.inline(f"✅ {p['key']} → {p['value']}", f"applyset:{p['key']}:{p['value']}")])
    buttons.append([Button.inline("❌ Abaikan semua", b"menu")])
    await _notify("\n".join(lines), buttons=buttons)
