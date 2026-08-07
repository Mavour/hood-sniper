"""Agent layer — Claude API sebagai otak pertimbangan entry & manajemen posisi.

Prinsip: LLM memutuskan DI DALAM pagar deterministik, tidak pernah melewatinya.
- Hard filter (mcap/liq/safety) jalan duluan; yang gagal tidak pernah sampai ke LLM.
- Semua output LLM di-clamp: size multiplier, TP/SL override, dsb.
- TP/SL deterministik tetap backstop terakhir di monitor loop.
- Setiap keputusan dicatat di decision log (bisa ditanya "kenapa?" via bot).
- Lessons diturunkan dari posisi closed dan di-inject ke prompt berikutnya.

Sync (requests) — panggil via asyncio.to_thread.
"""
import json
import time
import requests
from . import config, db, dexscreener as dx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"

# ---- pagar keras: LLM tidak bisa keluar dari rentang ini ----
SIZE_MULT_MIN = 0.25
TP1_RANGE = (0.20, 3.0)
TP2_RANGE = (0.30, 10.0)
SL_RANGE = (-0.60, -0.10)

SCREENER_SYSTEM = """You are the entry-decision module of hoodsniper, a meme token sniper on Robinhood Chain.
The "caller" field holds the track record of the specific person who posted this call (calls_bought, hit_tp1, full_tp, stopped) — a caller with many stopped and few TP is a strong reason to size down or SKIP; a proven caller justifies more conviction. A token call has ALREADY passed hard filters (mcap limit, min liquidity, dev-holding and holder-concentration checks). Your job is judgment on top of that: momentum quality, buy/sell flow, token age, source track record, and lessons from past trades.

Respond with ONLY a JSON object, no prose, no markdown fences:
{"action":"BUY"|"SKIP","conviction":0-100,"size_mult":0.25-MAXMULT,"tp1":0.2-3.0,"tp2":0.3-10.0,"sl":-0.6--0.1,"reason":"<=200 chars, bahasa Indonesia santai","risks":"<=120 chars","blacklist_caller":false}

Set "blacklist_caller": true ONLY when this caller's track record is clearly toxic (>=4 bought calls with >=75% stopped/rugged and zero full TPs). A blacklisted caller's future calls are auto-skipped without asking you — this is real authority, use it carefully.

Guidance:
- size_mult scales the base buy size. 1.0 = normal. Use <1 for weak/risky setups, >1 only for unusually strong ones.
- tp1/tp2 are profit fractions (0.5 = +50%), sl is loss fraction (-0.3 = -30%). Defaults are tp1=0.5, tp2=1.0, sl=-0.3; only deviate when data justifies it.
- Heavy sell pressure, dead volume, or a source with a bad track record are good reasons to SKIP or size down.
- You cannot exceed the caps; out-of-range values will be clamped anyway."""

MANAGER_SYSTEM = """You are the position-management module of hoodsniper. Deterministic TP/SL rules already run every cycle as a backstop; you add judgment between those thresholds (e.g. momentum died at +20%, exit early; or strong trend at +40%, hold for TP).

Respond with ONLY a JSON object:
{"action":"HOLD"|"SELL_HALF"|"SELL_ALL","reason":"<=200 chars, bahasa Indonesia santai"}

Be conservative: SELL actions execute real trades. Only sell when the data clearly says momentum is gone or risk spiked."""

LESSONS_SYSTEM = """You analyze closed trades of a meme token sniper and extract at most 3 short, concrete, actionable lessons (bahasa Indonesia). Respond ONLY with a JSON array of strings, e.g. ["...", "..."]. No prose."""


# ---------- LLM call (multi-provider) ----------
def _call(system, user, max_tokens=600):
    model = db.get("agent_model", str)
    if config.LLM_PROTOCOL == "anthropic":
        url = config.LLM_BASE_URL or ANTHROPIC_URL
        r = requests.post(url, timeout=45, headers={
            "x-api-key": config.LLM_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        })
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    # OpenAI-compatible: OpenAI / DeepSeek / GLM / Kimi / Qwen / OpenRouter / 9router / LM Studio
    base = (config.LLM_BASE_URL or OPENAI_DEFAULT_BASE).rstrip("/")
    r = requests.post(f"{base}/chat/completions", timeout=45, headers={
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }, json={
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    })
    r.raise_for_status()
    data = r.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    return msg.get("content") or ""


def _parse_json(text):
    """Toleran: markdown fence, teks pembungkus, 'Extra data', DAN output kepotong
    (truncated) karena max_tokens kekecilan — sering di provider verbose via 9router."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    # mulai dari kurung pembuka terluar yang muncul duluan
    a_obj, a_arr = t.find("{"), t.find("[")
    if a_arr != -1 and (a_obj == -1 or a_arr < a_obj):
        start = a_arr
    elif a_obj != -1:
        start = a_obj
    else:
        raise ValueError("tidak ada JSON di respons LLM")
    frag = t[start:]
    dec = json.JSONDecoder()
    # raw_decode: ambil objek/array valid PERTAMA, abaikan sisa ("Extra data")
    try:
        obj, _ = dec.raw_decode(frag)
        return obj
    except json.JSONDecodeError:
        pass
    # kemungkinan truncated → tutup string/kurung yang menggantung lalu retry
    repaired = _repair_truncated(frag)
    obj, _ = dec.raw_decode(repaired)
    return obj


def _repair_truncated(s):
    """Tambal JSON yang kepotong: tutup string terbuka + kurung yang belum ketutup.
    Untuk array of objects, buang elemen terakhir yang belum lengkap."""
    # buang trailing yang jelas nggantung
    s = s.rstrip()
    # hitung kurung di luar string
    depth_c = depth_s = 0
    in_str = esc = False
    last_complete = 0  # posisi setelah elemen array top-level terakhir yang komplit
    for i, ch in enumerate(s):
        if esc:
            esc = False; continue
        if ch == "\\" and in_str:
            esc = True; continue
        if ch == '"':
            in_str = not in_str; continue
        if in_str:
            continue
        if ch == "{": depth_c += 1
        elif ch == "}": depth_c -= 1
        elif ch == "[": depth_s += 1
        elif ch == "]": depth_s -= 1
        if ch == "," and depth_s == 1 and depth_c == 0:
            last_complete = i  # koma pemisah antar elemen array top-level
    if in_str:
        s += '"'
    # kalau array & masih ada elemen nggantung, potong sampai elemen komplit terakhir
    if s[:1] == "[" and (depth_c > 0 or depth_s > 1) and last_complete:
        s = s[:last_complete]
        depth_c = 0; depth_s = 1
    s += "}" * max(depth_c, 0)
    s += "]" * max(depth_s, 0)
    return s


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# ---------- context builders ----------
def _pair_context(pair):
    tx5 = (pair.get("txns") or {}).get("m5") or {}
    tx1h = (pair.get("txns") or {}).get("h1") or {}
    vol = pair.get("volume") or {}
    chg = pair.get("priceChange") or {}
    age_min = None
    if pair.get("pairCreatedAt"):
        age_min = int((time.time() * 1000 - pair["pairCreatedAt"]) / 60000)
    return {
        "symbol": (pair.get("baseToken") or {}).get("symbol"),
        "name": (pair.get("baseToken") or {}).get("name"),
        "mcap_usd": dx.mcap_of(pair),
        "liquidity_usd": dx.liq_usd(pair),
        "pool_age_minutes": age_min,
        "volume_usd": {"m5": vol.get("m5"), "h1": vol.get("h1")},
        "price_change_pct": {"m5": chg.get("m5"), "h1": chg.get("h1")},
        "txns_m5": {"buys": tx5.get("buys"), "sells": tx5.get("sells")},
        "txns_h1": {"buys": tx1h.get("buys"), "sells": tx1h.get("sells")},
        "dex": pair.get("dexId"), "pool_type": dx.pool_type(pair),
    }


def _memory_context():
    lessons = db.recent_lessons(5)
    decisions = db.recent_decisions(5)
    return {
        "lessons": [l["text"] for l in lessons],
        "recent_decisions": [
            {"symbol": d["symbol"], "action": d["action"], "conviction": d["conviction"],
             "reason": d["reason"], "outcome": d.get("outcome")}
            for d in decisions
        ],
    }


# ---------- entry decision (screener role) ----------
def decide_entry(pair, ca, source, safety_note, caller=None):
    base_usd = db.get("buy_usd")
    max_mult = db.get("agent_size_mult_max")
    ctx = {
        "token": _pair_context(pair),
        "safety_check": safety_note,
        "source": {"name": source["name"], "mcap_limit": source["mcap_max"],
                   "track_record": db.source_stats(source["name"])},
        "caller": caller,
        "base_buy_usd": base_usd,
        "defaults": {"tp1": db.get("tp1"), "tp2": db.get("tp2"), "sl": db.get("sl")},
        "memory": _memory_context(),
    }
    raw = _call(SCREENER_SYSTEM.replace("MAXMULT", str(max_mult)), json.dumps(ctx), 500)
    d = _parse_json(raw)

    action = "BUY" if str(d.get("action", "")).upper() == "BUY" else "SKIP"
    decision = {
        "action": action,
        "conviction": int(_clamp(d.get("conviction"), 0, 100, 50)),
        "size_mult": _clamp(d.get("size_mult"), SIZE_MULT_MIN, max_mult, 1.0),
        "tp1": _clamp(d.get("tp1"), *TP1_RANGE, db.get("tp1")),
        "tp2": _clamp(d.get("tp2"), *TP2_RANGE, db.get("tp2")),
        "sl": _clamp(d.get("sl"), *SL_RANGE, db.get("sl")),
        "reason": str(d.get("reason", ""))[:250],
        "risks": str(d.get("risks", ""))[:150],
        "blacklist_caller": bool(d.get("blacklist_caller")),
    }
    if decision["tp2"] <= decision["tp1"]:
        decision["tp2"] = decision["tp1"] + 0.25
    db.log_decision(ca=ca, symbol=ctx["token"]["symbol"] or "?", role="screener",
                    action=decision["action"], conviction=decision["conviction"],
                    size_mult=decision["size_mult"], reason=decision["reason"],
                    risks=decision["risks"])
    return decision


# ---------- position review (manager role) ----------
def review_position(pos, pair):
    price = float(pair.get("priceUsd") or 0)
    pnl = price / pos["entry_price_usd"] - 1 if price > 0 else 0
    ctx = {
        "position": {
            "symbol": pos["symbol"], "pnl_pct": round(pnl * 100, 1),
            "tp1_done": bool(pos["tp1_done"]),
            "minutes_held": int((time.time() - pos["ts"]) / 60),
            "thresholds": {"tp1": pos.get("tp1_x") or db.get("tp1"),
                           "tp2": pos.get("tp2_x") or db.get("tp2"),
                           "sl": pos.get("sl_x") or db.get("sl")},
        },
        "market": _pair_context(pair),
        "memory": {"lessons": [l["text"] for l in db.recent_lessons(5)]},
    }
    raw = _call(MANAGER_SYSTEM, json.dumps(ctx), 300)
    d = _parse_json(raw)
    action = str(d.get("action", "HOLD")).upper()
    if action not in ("HOLD", "SELL_HALF", "SELL_ALL"):
        action = "HOLD"
    reason = str(d.get("reason", ""))[:250]
    db.log_decision(ca=pos["ca"], symbol=pos["symbol"], role="manager",
                    action=action, conviction=0, size_mult=0, reason=reason, risks="")
    return action, reason


# ---------- lessons ----------
def derive_lessons():
    closed = db.closed_positions_with_decisions(20)
    if len(closed) < 5:
        return []
    raw = _call(LESSONS_SYSTEM, json.dumps(closed), 800)
    items = _parse_json(raw)
    out = []
    for t in items[:3]:
        t = str(t).strip()[:300]
        if t:
            db.add_lesson(t)
            out.append(t)
    return out


# ---------- 🎓 COACH: review harian yang bisa nyetel settings (dgn persetujuan) ----------
COACH_KEYS = {
    "buy_usd": (0.5, 10), "tp1": (0.2, 3.0), "tp2": (0.3, 10.0), "sl": (-0.6, -0.1),
    "tp1_sell_pct": (0.1, 0.9), "trail_pct": (0.1, 0.5), "min_liq_usd": (100, 10000),
    "dev_max_pct": (1, 20), "top10_max_pct": (10, 80), "agent_min_conviction": (0, 100),
}

COACH_SYSTEM = """You are the daily performance coach of hoodsniper, a meme sniper bot.
Given the last 24h recap, closed-trade breakdown, and current settings, propose AT MOST 3
concrete setting changes that would improve results. Only these keys are allowed: """ +     ", ".join(COACH_KEYS) + """.
Respond ONLY a JSON array: [{"key":"...","value":<number>,"why":"<=120 chars bahasa Indonesia"}].
Empty array [] if current settings look fine. Be conservative — one lever at a time beats
five. Never propose values outside sane ranges; they get clamped anyway."""


def coach(recap_text, current_settings):
    raw = _call(COACH_SYSTEM, json.dumps({
        "recap": recap_text,
        "settings": current_settings,
        "lessons": [l["text"] for l in db.recent_lessons(5)],
    }), 800)
    items = _parse_json(raw)
    out = []
    for it in items[:3]:
        key = str(it.get("key", ""))
        if key not in COACH_KEYS:
            continue
        lo, hi = COACH_KEYS[key]
        val = _clamp(it.get("value"), lo, hi, None)
        if val is None:
            continue
        out.append({"key": key, "value": round(val, 4), "why": str(it.get("why", ""))[:150]})
    return out


# ---------- 💬 TANYA: Q&A bebas grounded ke data bot ----------
QA_SYSTEM = """You are hoodsniper's assistant. Answer the admin's question in casual bahasa
Indonesia, grounded ONLY on the JSON data given (positions, stats, decisions, callers,
settings). If the data doesn't contain the answer, say so honestly — never invent numbers.
Keep it under 150 words."""


def tanya(question, context):
    return _call(QA_SYSTEM, json.dumps({"question": question, "data": context}), 700)
