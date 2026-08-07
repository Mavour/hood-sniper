# hoodsniper 🤖

> 📖 **Panduan lengkap ada di [PANDUAN.md](PANDUAN.md)** — baca itu untuk setup detail, penjelasan tiap menu, rollout, dan troubleshooting. README ini ringkasan cepat.

Multi-group meme sniper di **Robinhood Chain** dengan control panel gaya trading bot.

**Arsitektur hybrid:**
- 👂 **Telinga** — akun Telegram Kakak sendiri (Telethon, login QR). Bisa baca semua room yang Kakak sudah jadi member, **tanpa add bot apapun ke group**.
- 🎛 **Remote** — bot BotFather sebagai control panel: inline keyboard, panel posisi dengan PnL live, tombol sell manual, atur sources & settings dari HP tanpa SSH.

## Fitur

- **Multi-group / multi-topic** — tambah source sebanyak apapun, tiap source punya limit mcap sendiri. Tambah/hapus/on-off langsung dari bot, tanpa restart.
- **Panel posisi live** — PnL realtime per posisi, tombol `Sell 50%` / `Sell ALL` / link chart Dexscreener.
- **Settings via bot** — buy size, TP1/TP2/SL, slippage, min liquidity, interval — semua bisa diedit dari chat.
- **Pause/Resume** — stop entry sementara tanpa matiin proses.
- **Stats** — jumlah call, buy, TP/SL, estimasi PnL realized.
- **Auto TP/SL** — jual 50% di +50%, sisa di +100%, cut semua di -30% (default, bisa diubah).
- **Honeypot check** — simulasi sell setelah buy, alert kalau gagal.
- **Safety filter pre-buy** — skip token kalau dev pegang > 5% supply, atau top-10 holders (di luar pool/burn/dev) pegang > 40%. Data deployer & holders dari Blockscout Robinhood Chain. Threshold & on/off diatur dari ⚙️ Settings.
- Notif buy dilengkapi tombol sell langsung — lihat call masuk, satu tap buat exit.

## Agent mode (opsional) 🧠

Multi-provider: isi `LLM_PROTOCOL`, `LLM_API_KEY`, `LLM_BASE_URL` di `.env` (default Anthropic; protokol `openai` mendukung OpenAI/DeepSeek/GLM/Kimi/Qwen/OpenRouter/9router/LM Studio — contoh lengkap di `.env.example`), lalu nyalakan dari menu **🧠 Agent** di bot. Nama model diganti dari panel Agent, harus cocok dengan provider yang dipakai. Dua role:

- **Screener** — tiap call yang lolos hard filter dikirim ke Claude beserta konteks: momentum (volume/price change/buys-vs-sells 5m & 1h), umur pool, hasil safety check, track record room, keputusan terakhir, dan lessons. Claude balas keputusan terstruktur: BUY/SKIP, conviction, size multiplier, dan TP/SL khusus posisi itu — semua di-clamp dalam pagar (max multiplier, rentang TP/SL).
- **Manager** — tiap N menit review posisi terbuka: momentum mati di +20%? exit duluan. Trend kuat? hold. TP/SL deterministik tetap jalan sebagai backstop.

Fitur:
- **Dry run default ON** — agent kasih keputusan tanpa eksekusi, buat validasi dulu sebelum live.
- **Decision log** — semua keputusan + alasan tercatat, lihat via 📜 di panel Agent; outcome (tp2/sl) ditulis balik ke keputusan asalnya.
- **Lessons** — dari ≥5 posisi closed, agent menurunkan max 3 pelajaran konkret yang di-inject ke prompt berikutnya (tombol 🧠 Derive lessons).
- Model default `claude-haiku-4-5` (cepat & murah); bisa diganti dari panel.
- Kalau API error/timeout → **fallback ke rule-based**, entry tetap jalan pakai rule lama.

## Setup

```bash
cd hoodsniper2
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# alamat Uniswap chain 4663
npm i @uniswap/sdk-core
node tools/fetch_addresses.mjs      # copy hasilnya ke .env

# bikin control bot
# 1. chat @BotFather → /newbot → copy token → BOT_TOKEN di .env
# 2. chat @userinfobot → copy user id → ADMIN_ID di .env
#    (cuma ADMIN_ID yang bisa mengontrol bot; orang lain di-reject)

cp .env.example .env && nano .env   # isi PRIVATE_KEY, API ID/hash, token, admin id

# login akun Telegram (QR) — sekali saja
./venv/bin/python bot.py login

# jalankan
pm2 start ecosystem.config.js
pm2 save && pm2 startup
```

Lalu buka bot Kakak di Telegram → `/start` → panel muncul.

> ⚠️ Kirim `/start` ke bot minimal sekali — bot Telegram tidak bisa memulai chat duluan.

## Menu bot

```
🤖 hoodsniper v2
status: ▶️ AKTIF
👛 0xAbC…       ⛽ 0.045 ETH
📊 open: 3      📡 sources: 2

[📊 Posisi]  [📡 Sources]
[⚙️ Settings] [📈 Stats]
[⏸ Pause]    [🔄 Refresh]
```

**Tambah source** (dari menu 📡 Sources → ➕):
```
https://t.me/c/2103131992/250551 30000 Degen Hood
-1001234567890 15000 Alpha Group
```
Format: `<link_topic_atau_chat_id> <mcap_max> <nama>`. Paste link topic `t.me/c/...` langsung — chat id & topic id di-parse otomatis. Tanpa topic = semua message di group dipantau. Syaratnya cuma satu: **akun Kakak harus sudah member group itu.**

Source lama (Degen Room $30k + RH Member Call $10k) otomatis ke-seed saat pertama jalan.

## Catatan

- **Wallet agent terpisah**, isi secukupnya. `chmod 600 .env`. Anggap dana di dalamnya siap hangus — call low cap 90% rug.
- **Rate limit**: makin banyak source ber-volume tinggi, makin sering hit Dexscreener & RPC. Kalau mulai error 429, ganti `RPC_URL` ke endpoint dedicated dan naikkan `poll_seconds`.
- **File penting**: `hoodsniper.session`, `hoodsniper_bot.session`, `hoodsniper.db` — backup kalau pindah VPS, jangan commit ke git.
- Dedupe global per CA — token yang sama di-call dua room berbeda cuma dibeli sekali.

## Perintah VPS

```bash
pm2 logs hoodsniper          # log realtime (termasuk [skip])
pm2 restart hoodsniper
sqlite3 hoodsniper.db "SELECT symbol,status,realized_eth FROM positions;"
```
