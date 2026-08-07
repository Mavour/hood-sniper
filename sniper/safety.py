"""Safety filter pre-buy: dev holding & konsentrasi top holders.

Sumber data: Blockscout Robinhood Chain (deployer & daftar holder) + RPC
(balanceOf/totalSupply). Sync — panggil via asyncio.to_thread.
"""
import requests
from web3 import Web3
from . import config, db

_S = requests.Session()
_S.headers.update({"User-Agent": "hoodsniper/2.1"})

DEAD = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}

TOTAL_SUPPLY_ABI = [{
    "name": "totalSupply", "type": "function", "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "uint256"}],
}]


def _get(path, params=None):
    r = _S.get(f"{config.BLOCKSCOUT_API}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def creator_of(ca: str):
    """Alamat deployer kontrak token (None kalau tidak ketahuan)."""
    try:
        d = _get(f"/addresses/{ca}")
        return d.get("creator_address_hash")
    except Exception:
        return None


def _total_supply(trader, ca: str) -> int:
    c = trader.w3.eth.contract(Web3.to_checksum_address(ca), abi=TOTAL_SUPPLY_ABI)
    return c.functions.totalSupply().call()


def dev_pct(trader, ca: str) -> float:
    """% supply yang dipegang deployer. 0 kalau deployer tidak ketemu."""
    dev = creator_of(ca)
    if not dev or dev.lower() in DEAD:
        return 0.0
    ts = _total_supply(trader, ca)
    if ts <= 0:
        return 0.0
    bal = trader.erc20(ca).functions.balanceOf(Web3.to_checksum_address(dev)).call()
    return bal / ts * 100


def top_holders_pct(trader, ca: str, exclude: set, n: int = 10) -> float:
    """% supply gabungan n holder teratas, exclude pool/burn/dev sudah dihitung terpisah."""
    try:
        d = _get(f"/tokens/{ca}/holders")
    except Exception:
        return 0.0  # data holder belum tersedia (token terlalu baru) — jangan blokir
    ts = _total_supply(trader, ca)
    if ts <= 0:
        return 0.0
    ex = {a.lower() for a in exclude} | DEAD
    vals = []
    for item in d.get("items") or []:
        addr = ((item.get("address") or {}).get("hash") or "").lower()
        if addr in ex:
            continue
        try:
            vals.append(int(item.get("value") or 0))
        except (TypeError, ValueError):
            continue
        if len(vals) >= n:
            break
    return sum(vals) / ts * 100


def check(trader, ca: str, pair) -> tuple[bool, str]:
    """Return (lolos, alasan). Dipanggil sebelum buy."""
    if db.get("safety_on", str) != "1":
        return True, "safety off"

    max_dev = db.get("dev_max_pct")
    max_top = db.get("top10_max_pct")

    # fail_closed (default 1): kalau data holder/dev TIDAK bisa diverifikasi
    # (Blockscout error/timeout) → SKIP, jangan lolos. Set 0 utk perilaku
    # agresif lama (lolos kalau API ngadat).
    fail_closed = db.get("safety_fail_closed", str) == "1"

    # ---- dev holding ----
    dev_failed = False
    try:
        dp = dev_pct(trader, ca)
    except Exception:
        dp, dev_failed = -1.0, True
    if not dev_failed and dp > max_dev:
        return False, f"dev pegang {dp:.1f}% supply (max {max_dev:.0f}%)"

    # ---- top-10 concentration ----
    top_failed = False
    exclude = {pair.get("pairAddress", ""), ca}
    try:
        dev = creator_of(ca)
        if dev:
            exclude.add(dev)
    except Exception:
        pass
    try:
        tp = top_holders_pct(trader, ca, exclude)
        if tp <= 0:
            top_failed = True  # 0% = data holder belum ke-index / kosong, bukan "aman"
    except Exception:
        tp, top_failed = -1.0, True
    if not top_failed and tp > max_top:
        return False, f"top10 holders pegang {tp:.1f}% (max {max_top:.0f}%)"

    # ---- kebijakan saat verifikasi gagal ----
    if dev_failed or top_failed:
        which = []
        if dev_failed: which.append("dev")
        if top_failed: which.append("top10")
        w = "+".join(which)
        if fail_closed:
            return False, f"data {w} tidak bisa diverifikasi (Blockscout) — SKIP (fail-closed)"
        return True, f"⚠️ {w} tidak terverifikasi, lolos (fail-open)"

    return True, f"dev {dp:.1f}% · top10 {tp:.1f}%"
