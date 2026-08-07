import os
from dotenv import load_dotenv

load_dotenv()

def _i(key, default):
    return int(os.getenv(key, default))

# ---- Chain / RPC ----
RPC_URL       = os.getenv("RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
CHAIN_ID      = _i("CHAIN_ID", 4663)
PRIVATE_KEY   = os.getenv("PRIVATE_KEY", "")

# ---- Uniswap di Robinhood Chain ----
SWAP_ROUTER02 = os.getenv("SWAP_ROUTER02", "")
V2_ROUTER     = os.getenv("V2_ROUTER", "")

# ---- Telegram userbot (Telethon, QR login) ----
TG_API_ID     = _i("TG_API_ID", 0)
TG_API_HASH   = os.getenv("TG_API_HASH", "")
SESSION_NAME  = os.getenv("SESSION_NAME", "hoodsniper")

# ---- Control bot (BotFather) ----
BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
ADMIN_ID      = _i("ADMIN_ID", 0)          # user id Telegram Kakak (cek via @userinfobot)

# ---- Seed source awal (opsional, dipakai kalau tabel sources masih kosong) ----
SEED_GROUP_ID    = _i("SEED_GROUP_ID", -1002103131992)
SEED_TOPIC_DEGEN = _i("SEED_TOPIC_DEGEN", 250551)
SEED_TOPIC_MEMBER= _i("SEED_TOPIC_MEMBER", 395868)

# ---- Dexscreener ----
DEX_CHAIN_MATCH = os.getenv("DEX_CHAIN_MATCH", "robinhood")

# ---- Blockscout (safety filter) ----
BLOCKSCOUT_API = os.getenv("BLOCKSCOUT_API", "https://robinhoodchain.blockscout.com/api/v2")
EXPLORER_URL   = os.getenv("EXPLORER_URL", "https://robinhoodchain.blockscout.com")

# ---- Agent (LLM multi-provider) ----
# LLM_PROTOCOL: "anthropic" atau "openai" (OpenAI-compatible: OpenAI/DeepSeek/GLM/
# Kimi/Qwen/OpenRouter/9router/LM Studio — tinggal ganti LLM_BASE_URL)
LLM_PROTOCOL = os.getenv("LLM_PROTOCOL", "anthropic").lower()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY  = os.getenv("LLM_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_KEY = LLM_API_KEY  # alias backwards-compat

DB_PATH = os.getenv("DB_PATH", "hoodsniper.db")

# Default settings runtime (masuk DB saat init pertama, selanjutnya diatur via bot)
SETTING_DEFAULTS = {
    "buy_usd":        "1.0",
    "tp1":            "0.50",
    "tp2":            "1.00",
    "sl":             "-0.30",
    "tp1_sell_pct":   "0.50",
    "tp_verify":      "1",
    "rug_liq_drop":   "0.40",
    "trail_on":       "1",
    "trail_pct":      "0.25",
    "tp2_on":         "1",
    "recap_on":       "1",
    "recap_hour":     "7",
    "buy_slippage":   "0.30",
    "sell_slippage":  "0.35",
    "min_liq_usd":    "500",
    "poll_seconds":   "20",
    "paused":         "0",
    "safety_on":      "1",
    "dev_max_pct":    "5",
    "top10_max_pct":  "40",
    "safety_fail_closed": "1",
    "agent_on":       "0",
    "agent_manage_on": "0",
    "agent_dry_run":  "1",
    "agent_model":    "claude-haiku-4-5",
    "agent_size_mult_max": "2",
    "agent_review_min": "10",
    "agent_min_conviction": "55",
    "agent_blacklist": "1",
    "agent_auto_insting": "1",
    "agent_coach":    "1",
    "circuit_breaker_on": "1",
    "daily_loss_limit_eth": "0",
    "circuit_breaker_window_min": "1440",
}

SETTING_LABELS = {
    "buy_usd":       "💵 Buy size (USD)",
    "tp1":           "🎯 TP1 (0.5 = +50%)",
    "tp2":           "🎯 TP2 (1.0 = +100%)",
    "sl":            "🛑 SL (-0.3 = -30%)",
    "tp1_sell_pct":  "📤 Jual di TP1 (0.5 = 50%)",
    "tp_verify":     "🫧 Verifikasi TP anti-wick (1/0)",
    "rug_liq_drop":  "🚨 Rug: liq drop trigger (0.4=40%)",
    "trail_on":      "📈 Trailing stop setelah TP1 (1/0)",
    "trail_pct":     "📈 Trailing % dari puncak (0.25)",
    "tp2_on":        "🎯 TP2 aktif (0 = biarkan runner lari)",
    "recap_on":      "🌅 Rekap harian (1/0)",
    "recap_hour":    "🌅 Jam rekap WIB (0-23)",
    "buy_slippage":  "📈 Slippage buy",
    "sell_slippage": "📉 Slippage sell",
    "min_liq_usd":   "💧 Min liquidity (USD)",
    "poll_seconds":  "⏱ Interval cek harga (dtk)",
    "safety_on":     "🛡 Safety filter (1=on 0=off)",
    "dev_max_pct":   "🕵️ Max dev holding (%)",
    "top10_max_pct": "🐋 Max top10 holders (%)",
    "safety_fail_closed": "🔒 Fail-closed kalau data gagal (1/0)",
    "agent_size_mult_max": "🧠 Max size multiplier agent",
    "agent_review_min": "🧠 Interval review agent (menit)",
    "agent_min_conviction": "👁 Min conviction buat entry (0-100)",
    "circuit_breaker_on": "🔴 Circuit breaker (1/0)",
    "daily_loss_limit_eth": "🔴 Limit rugi (ETH, 0=off)",
    "circuit_breaker_window_min": "🔴 Window breaker (menit, 1440=24j)",
}

def validate():
    missing = [k for k, v in {
        "PRIVATE_KEY": PRIVATE_KEY, "SWAP_ROUTER02": SWAP_ROUTER02,
        "V2_ROUTER": V2_ROUTER, "TG_API_ID": TG_API_ID,
        "TG_API_HASH": TG_API_HASH, "BOT_TOKEN": BOT_TOKEN,
        "ADMIN_ID": ADMIN_ID,
    }.items() if not v]
    if missing:
        raise SystemExit(f"[config] .env belum lengkap: {', '.join(missing)}")
