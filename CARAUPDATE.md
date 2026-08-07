# 🔄 CARA UPDATE hoodsniper (Tanpa Login Ulang)

Panduan update untuk deployment yang **sudah jalan** — sudah login QR, sudah ada
posisi & settings. Tujuan: ganti kodenya saja, **jangan sampai** kehilangan
sesi login, database, atau `.env`.

---

## ⚠️ Prinsip utama

Login QR & semua data Kakak TIDAK ada di dalam kode — ada di 4 file ini:

| File | Isi | Kalau ketimpa |
|---|---|---|
| `hoodsniper.session` | Login akun Telegram (QR) | ❌ harus scan QR ulang |
| `hoodsniper_bot.session` | Login control bot | ❌ bot mati sampai re-auth |
| `hoodsniper.db` | Posisi, settings, skor caller, jejak, insting | ❌ semua histori hilang |
| `.env` | Private key wallet + config | ❌ fatal, harus isi ulang |

Zip update **tidak berisi** 4 file itu (cuma `.env.example`, bukan `.env`).
Jadi selama Kakak cuma meng-copy file KODE dan tidak menyentuh 4 file di atas,
login & data aman. Panduan di bawah dibuat supaya itu otomatis terjaga.

---

## 📋 Langkah update (aman, dengan backup)

```bash
# Masuk ke direktori tempat folder hoodsniper berada
cd ~

# 1. Stop bot dulu (biar tidak trading saat file diganti)
pm2 stop hoodsniper

# 2. Backup folder lama — jaring pengaman kalau ada apa-apa
cp -r hoodsniper_final hoodsniper_backup_$(date +%F)

# 3. Extract update ke folder sementara
mkdir -p hoodsniper_new && cd hoodsniper_new
unzip -o ~/hoodsniper.zip

# 4. Copy HANYA kode + panduan ke folder lama.
#    Session, DB, dan .env TIDAK ikut ter-copy karena tidak disebut di sini.
cp bot.py                ../hoodsniper_final/
cp -r sniper/            ../hoodsniper_final/
cp -r tools/             ../hoodsniper_final/
cp ecosystem.config.js   ../hoodsniper_final/
cp PANDUAN.md README.md CARAUPDATE.md ../hoodsniper_final/ 2>/dev/null

# 5. (opsional) lihat perbedaan .env — kalau ada setting baru di .env.example
cd ../hoodsniper_final
diff <(grep -oE '^[A-Z_]+=' .env | sort) \
     <(grep -oE '^[A-Z_]+=' ../hoodsniper_new/.env.example | sort)
#   Kalau ada baris muncul yang belum ada di .env Kakak, tambahkan manual.

# 6. Restart & pantau
pm2 restart hoodsniper
pm2 logs hoodsniper
```

---

## ✅ Cek login masih valid

Lihat output `pm2 logs hoodsniper`:

- **Berhasil (login aman):**
  ```
  [hoodsniper] running. wallet=0xAbC...
  ```
  Muncul tanpa minta QR → sesi kebaca dengan benar. Selesai.

- **Minta QR lagi:**
  Berarti file session tidak ada di folder. Jangan panik — restore dari backup:
  ```bash
  cp hoodsniper_backup_*/hoodsniper.session      hoodsniper_final/
  cp hoodsniper_backup_*/hoodsniper_bot.session  hoodsniper_final/
  pm2 restart hoodsniper
  ```

---

## 🗄️ Soal database (auto-migrate)

Update ini menambah kolom DB baru (`peak_price`, `closed_at`, `buy_tx`, dll).
**Tidak perlu hapus `hoodsniper.db`.** Saat bot start, kode menjalankan
`ALTER TABLE ... ` dalam try/except — kolom baru ditambahkan otomatis ke DB
lama tanpa merusak data yang sudah ada. Posisi terbuka, settings, skor caller,
dan jejak semuanya tetap utuh.

---

## ⚙️ Setting baru di update ini

Muncul otomatis dengan nilai default di panel ⚙️ Settings & 🧠 Otak. Tidak perlu
diisi manual, tapi bisa disesuaikan:

| Setting | Default | Fungsi |
|---|---|---|
| 🫧 Verifikasi TP anti-wick | 1 | Tahan TP kalau wick tanpa depth |
| 🚨 Rug: liq drop trigger | 0.40 | Emergency exit kalau liq anjlok 40% |
| 📈 Trailing stop setelah TP1 | 1 | Kunci profit + ikuti runner |
| 📈 Trailing % dari puncak | 0.25 | Jarak trailing dari harga tertinggi |
| 🎯 TP2 aktif | 1 | Set 0 kalau mau runner lari (exit via trailing) |
| 🌅 Rekap harian | 1 | Kirim rekap tiap pagi |
| 🌅 Jam rekap WIB | 7 | Jam kirim rekap (0-23) |

---

## 🔁 Rollback (kalau update bermasalah)

```bash
pm2 stop hoodsniper
rm -rf hoodsniper_final
mv hoodsniper_backup_$(date +%F) hoodsniper_final
cd hoodsniper_final && pm2 restart hoodsniper
```

Karena session & DB tidak pernah disentuh saat update, rollback kode tidak
mempengaruhi login maupun data.

---

## Ringkasan singkat

1. `pm2 stop hoodsniper`
2. backup folder
3. extract zip ke folder baru
4. copy **hanya** `bot.py`, `sniper/`, `tools/`, `ecosystem.config.js`, file `.md`
5. `pm2 restart hoodsniper` → cek log muncul `running. wallet=0x...`

Session, DB, dan `.env` tidak pernah disentuh → **tidak perlu login QR ulang.**
