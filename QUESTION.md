# ❓ QUESTION.md — Tanya Jawab hoodsniper

Kumpulan pertanyaan yang sering muncul soal setup & aturan bot. Dibikin biar
member tinggal baca, nggak nanya berulang. Urut dari setup → aturan trading →
fitur → keamanan → troubleshooting.

---

## 🛠️ SETUP & INSTALASI

### Q: SWAP_ROUTER02 & V2_ROUTER nyarinya di mana?
Ini alamat kontrak Uniswap di Robinhood Chain (chain ID 4663). Tiga cara:

1. **Otomatis (paling gampang):**
   ```bash
   npm i @uniswap/sdk-core
   node tools/fetch_addresses.mjs
   ```
   Kalau chain 4663 ada di SDK, alamat langsung keluar → copy ke `.env`.

2. **Manual dari sumber resmi:** buka halaman "Uniswap Contract Deployments"
   di `docs.uniswap.org`, cari Robinhood Chain (4663). Ambil `SwapRouter02` →
   `SWAP_ROUTER02`, dan `UniswapV2Router02` → `V2_ROUTER`.

3. **Verifikasi via explorer:** buka `robinhoodchain.blockscout.com`, buka satu
   transaksi swap token yang aktif, lihat kontrak router yang dipanggil.

**Penting:** router itu alamat PUBLIK, sama untuk semua orang di chain 4663.
Sekali dapat, dipakai semua. Yang beda per orang cuma PRIVATE_KEY.

### Q: TG_API_ID & TG_API_HASH ambil di mana?
Dari https://my.telegram.org → login pakai nomor Telegram → "API development
tools" → bikin app → copy `api_id` dan `api_hash`.

### Q: BOT_TOKEN dan ADMIN_ID dapat dari mana?
- **BOT_TOKEN**: chat [@BotFather](https://t.me/BotFather) → `/newbot` → ikuti
  langkahnya → copy token yang dikasih.
- **ADMIN_ID**: chat [@userinfobot](https://t.me/userinfobot) → dia balas user
  id angka Kakak. Cuma id ini yang bisa mengontrol bot.

### Q: RPC_URL-nya pakai apa?
Default `https://rpc.mainnet.chain.robinhood.com` sudah diisi. Kalau sering kena
rate limit (error 429 di log), ganti ke RPC dedicated (Alchemy/QuickNode/
Chainstack yang support Robinhood Chain).

### Q: Wallet agent itu apa? Pakai wallet utama boleh?
**JANGAN pakai wallet utama.** Wallet agent = wallet BARU khusus bot. Private
key-nya disimpan di VPS, jadi anggap dana di dalamnya siap hangus. Isi
secukupnya saja — 0.02–0.05 ETH cukup buat banyak entry + gas.

### Q: Cara login Telegram gimana? Kok pakai QR?
Sekali saja, jalankan `./venv/bin/python bot.py login`. QR muncul di terminal →
scan dari HP: Settings → Devices → Link Desktop Device. Pakai QR (userbot)
karena bot BotFather biasa TIDAK bisa baca room orang lain — hanya akun sendiri
yang bisa mirror room member-only tanpa di-invite.

### Q: Setelah login, ngapain lagi?
`pm2 start ecosystem.config.js` → buka bot Kakak di Telegram → kirim `/start`.
Panel muncul. (Bot Telegram tidak bisa memulai chat duluan, jadi `/start` wajib
sekali.)

---

## 🎯 ATURAN TRADING

### Q: Bot beli token apa saja? Aturannya apa?
Token yang di-call di room yang Kakak pantau, LOLOS semua filter ini berurutan:
1. **mcap** di bawah limit source (contoh: Degen Room ≤ $30k, Member Call ≤ $10k)
2. **liquidity** ≥ minimum (default $500)
3. **dev holding** ≤ batas (default 5% supply)
4. **top-10 holders** ≤ batas (default 40%)
5. **honeypot check** — simulasi sell setelah beli

### Q: Sekali beli berapa?
Default **$1 worth of ETH per call**. Bisa diubah di ⚙️ Settings → Buy size.
Strategi spray: banyak taruhan kecil, satu runner nutup yang rug.

### Q: Kapan bot jual?
Otomatis, tanpa perlu Kakak pencet apa-apa:
- **+50% (TP1)** → jual 50%, sisanya jadi moonbag
- **+100% (TP2)** → jual sisa, posisi close
- **-30% (SL)** → cut loss semua
Semua angka bisa diubah di ⚙️ Settings.

### Q: Apa itu trailing stop?
Setelah TP1 kena, sisa posisi TIDAK lagi pakai SL -30% dari entry. Gantinya:
stop naik ke breakeven (minimal balik modal) lalu mengikuti harga tertinggi.
Keluar hanya kalau turun 25% (default) dari puncak. Gunanya: profit yang sudah
di tangan tidak balik jadi rugi, DAN runner yang lari terus ke-ikutin.

### Q: Kalau mau runner lari jauh (nggak dipotong di +100%) gimana?
⚙️ Settings → 🎯 TP2 aktif = **0**. TP2 mati, moonbag dilepas lari, exit-nya
sepenuhnya lewat trailing stop.

### Q: Bot bisa jual otomatis pas profit kan?
Bisa, itu default-nya. Loop monitor cek harga tiap 20 detik dan eksekusi TP/SL
sendiri. Tombol Sell di panel cuma buat override manual kalau mau keluar duluan.

---

## 🧠 FITUR PINTAR (OTAK / LLM)

### Q: Bisa trading pakai bantuan AI/LLM?
Bisa, opsional. Isi `LLM_API_KEY` di `.env`, nyalakan dari menu 🧠 Otak. AI
menambah pertimbangan (momentum, skor caller, insting) di atas rule dasar.
Tanpa LLM, bot tetap jalan penuh pakai rule-based.

### Q: LLM-nya support apa aja?
Anthropic + semua yang OpenAI-compatible: OpenAI, DeepSeek, GLM, Kimi, Qwen,
OpenRouter, 9router, LM Studio. Cukup set `LLM_PROTOCOL`, `LLM_API_KEY`,
`LLM_BASE_URL`. Contoh lengkap di `.env.example`.

### Q: Pakai 9router, setting API key-nya gimana?
9router itu OpenAI-compatible, jadi:
```
LLM_PROTOCOL=openai
LLM_BASE_URL=https://api.9router.co/v1     (cek base URL persis di dashboard 9router)
LLM_API_KEY=<api key dari 9router>
```
Lalu nama model diganti dari panel 🧠 Otak → 🤖 Ganti model, isi sesuai model
yang tersedia di 9router (misal `deepseek-chat`, `gemini-2.5-flash`, dll).
Keunggulan 9router: satu key, ganti-ganti model dari panel tanpa daftar API
satu-satu. Kalau `/tanya` atau latih insting jalan, berarti koneksi sudah benar.

### Q: Error "gagal melatih insting: Extra data..." / "Expecting value"?
Itu respons LLM kepotong karena output-nya panjang (sering di provider verbose
via 9router). Sudah diperbaiki di update terbaru: parser sekarang tahan output
kepotong DAN limit token dinaikkan. Update ke versi terbaru, restart. Kalau
masih muncul, model-nya mungkin nggak patuh format JSON — ganti ke model lain
dari panel Otak (Haiku / DeepSeek-chat / Gemini Flash paling patuh).

### Q: LLM key doang cukup buat trading?
**Tidak.** LLM cuma otak pengambil keputusan. Yang eksekusi jual-beli tetap
butuh PRIVATE_KEY (wallet) + router Uniswap + RPC. LLM opsional; wallet & router
wajib.

### Q: Model LLM apa yang bagus buat bot ini?
Untuk keputusan entry (butuh cepat): Gemini Flash-Lite, DeepSeek V4-Flash, atau
Claude Haiku. JANGAN model reasoning/thinking — terlalu lambat, entry telat.

### Q: Apa itu Skor Caller?
Bot mencatat SIAPA yang posting tiap call (bukan cuma room-nya) lalu menghitung
reputasi tiap caller: berapa call jadi TP, berapa jadi SL. Caller yang sering
cuan bisa dikasih size lebih besar; yang sering rug di-skip. Lihat di 📈 Stats.

### Q: Mode Latihan (dry run) itu apa?
Otak kasih keputusan lengkap + alasan TANPA eksekusi beneran. Wajib dipakai
dulu minimal seminggu buat validasi sebelum live. Cek 👣 Jejak tiap hari.

---

### Q: Blacklist caller itu apa? Bisa dimatikan?
Mata (LLM) boleh mem-blacklist caller yang track record-nya jelas beracun —
call dia berikutnya auto-skip. Bisa di-ON/OFF dari panel 🧠 Otak. **OFF =
daftar tidak dipakai dan Mata tidak bisa menambah caller baru**, tapi daftar
tetap tersimpan (ON lagi → aktif lagi). Hapus permanen per-caller lewat tombol
♻️ Pulihkan. Saat Mode Latihan, blacklist tidak pernah dieksekusi beneran.

### Q: Kenapa entry di-skip padahal Mata bilang BUY?
Cek conviction-nya. BUY dengan conviction di bawah `agent_min_conviction`
(default 55) otomatis di-skip — notifnya "MATA RAGU". Turunkan threshold di
⚙️ Settings kalau mau lebih agresif.

### Q: Coach itu apa? Dia bisa ubah setting sendiri?
Coach = analisa harian Otak yang MENGUSULKAN perubahan setting (max 3) setelah
rekap pagi. TIDAK auto-apply — usulan datang dengan tombol ✅, Kakak yang
memutuskan. Usulan dibatasi whitelist + clamp, jadi Coach tidak bisa menyentuh
private key, router, atau nilai ekstrem.

### Q: /tanya itu apa?
Command Q&A bebas: `/tanya kenapa kemarin banyak SL?` — Otak menjawab
berdasarkan data asli bot (bukan mengarang). Butuh LLM_API_KEY terisi.

## 🛡️ KEAMANAN

### Q: Aman nggak private key di VPS?
Selama VPS aman & `.env` di-`chmod 600`, oke. Tapi tetap: wallet agent terpisah,
isi secukupnya. Jangan pernah taruh wallet utama.

### Q: Ada yang minta private key/mnemonic saya, boleh dikasih?
**TIDAK. TIDAK PERNAH. KE SIAPAPUN.** Termasuk ke admin/siapapun yang ngaku
admin. Private key = akses penuh ke dana. Yang minta private key = scammer,
titik. Bot ini pun tidak pernah minta private key member — Kakak isi sendiri di
`.env` VPS masing-masing.

### Q: Kalau private key bocor gimana?
Dana di wallet itu bisa langsung dikuras. Makanya wallet agent isi secukupnya,
dan simpan mnemonic + private key di tempat aman offline.

### Q: Cuma ADMIN_ID yang bisa kontrol bot?
Ya. User lain yang coba akses bot langsung di-reject. Pastikan ADMIN_ID diisi
benar (dari @userinfobot).

---

## 🔧 TROUBLESHOOTING

### Q: Bot nggak balas /start?
ADMIN_ID salah. Cek ulang user id via @userinfobot, betulkan di `.env`,
`pm2 restart hoodsniper`.

### Q: Semua call ke-skip terus?
Cek: limit mcap terlalu ketat? `DEX_CHAIN_MATCH` benar (harus "robinhood")?
Lihat `pm2 logs hoodsniper` — alasan skip tertulis di situ ([skip], [safety-skip]).

### Q: Error "Could not transact with/call contract function, is contract deployed correctly and chain synced?"
Artinya bot memanggil alamat yang TIDAK ADA kontraknya di chain ini. 99%
penyebab: `SWAP_ROUTER02` / `V2_ROUTER` di `.env` salah (misal tercopy alamat
Base/Ethereum, bukan Robinhood Chain 4663), atau RPC nyambung ke chain lain.
Sejak update terbaru, bot menolak start dengan pesan jelas kalau router salah.
Ambil alamat benar via `node tools/fetch_addresses.mjs`, verifikasi contract-nya
di robinhoodchain.blockscout.com (harus verified, nama SwapRouter02 /
UniswapV2Router02), isi ulang `.env`, restart.

### Q: Error 429 di log?
RPC atau Dexscreener kena rate limit. Ganti RPC dedicated, naikkan interval cek
harga di Settings.

### Q: PnL di bot beda sama transaksi asli di explorer?
Update terbaru sudah pakai PnL realized asli (dari saldo wallet, bukan harga
layar). Notif nampilin "layar X% → realized Y%". Kalau beda jauh = pool tipis
(price impact), warning otomatis muncul. Tap tombol 🔍 Tx buat verifikasi
langsung di Blockscout.

### Q: Kok TP kadang "ditahan" (notif 🫧)?
Itu verifikasi anti-wick. Harga di layar naik tinggi tapi likuiditas nggak cukup
buat jual bag di harga itu (wick kosong). Bot nahan biar nggak jual di harga
khayalan. Posisi tetap dimonitor; kalau depth beneran datang, TP eksekusi.

### Q: Cara update bot tanpa login QR ulang?
Baca CARAUPDATE.md. Intinya: copy HANYA file kode (`bot.py`, `sniper/`,
`tools/`, dll), JANGAN timpa `hoodsniper.session`, `hoodsniper.db`, dan `.env`.
Kolom DB baru auto-migrate saat restart.

### Q: File apa yang harus di-backup kalau pindah VPS?
`hoodsniper.session`, `hoodsniper_bot.session`, `hoodsniper.db`, dan `.env`.
Jangan commit ke git.

---

## ⚠️ EKSPEKTASI JUJUR

### Q: Bot ini pasti cuan?
**Tidak ada yang pasti cuan.** Call room low-cap mayoritas rug. Bot cuma bantu:
entry disiplin & kecil, exit otomatis, filter safety, deteksi rug/honeypot.
Anggap dana wallet agent = dana yang siap hangus. Ini bukan financial advice.

### Q: Kenapa "TP kena" kelihatan lebih sedikit dari dulu?
Karena update PnL jujur + anti-wick. Dulu banyak "TP" yang sebenarnya jual di
harga khayalan (wick kosong) tapi ke-hitung cuan. Sekarang angkanya jujur.
Turunnya angka = laporan makin akurat, bukan bot makin jelek.
