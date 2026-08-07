"""Dexscreener API helper (sync, panggil via asyncio.to_thread)."""
import requests
from . import config

BASE = "https://api.dexscreener.com/latest/dex"
_S = requests.Session()
_S.headers.update({"User-Agent": "hoodsniper/1.0"})


def token_pairs(ca: str):
    """Semua pair untuk sebuah token CA (lintas chain)."""
    r = _S.get(f"{BASE}/tokens/{ca}", timeout=10)
    r.raise_for_status()
    return (r.json() or {}).get("pairs") or []


def best_pair(ca: str):
    """Pair terbaik di Robinhood Chain: liquidity USD tertinggi."""
    pairs = [
        p for p in token_pairs(ca)
        if config.DEX_CHAIN_MATCH in (p.get("chainId") or "").lower()
        and (p.get("baseToken", {}).get("address", "").lower() == ca.lower())
    ]
    if not pairs:
        return None
    pairs.sort(key=lambda p: (p.get("liquidity") or {}).get("usd") or 0, reverse=True)
    return pairs[0]


def get_pair(chain_slug: str, pair_address: str):
    r = _S.get(f"{BASE}/pairs/{chain_slug}/{pair_address}", timeout=10)
    r.raise_for_status()
    d = r.json() or {}
    pairs = d.get("pairs") or ([d["pair"]] if d.get("pair") else [])
    return pairs[0] if pairs else None


def mcap_of(pair) -> float:
    return float(pair.get("marketCap") or pair.get("fdv") or 0)


def liq_usd(pair) -> float:
    return float((pair.get("liquidity") or {}).get("usd") or 0)


def eth_usd(pair) -> float:
    """Harga ETH (native) dalam USD, diturunkan dari priceUsd / priceNative."""
    pu = float(pair.get("priceUsd") or 0)
    pn = float(pair.get("priceNative") or 0)
    return pu / pn if pu and pn else 0.0


def pool_type(pair) -> str:
    """'v2' atau 'v3' berdasarkan labels Dexscreener. Default v3."""
    labels = [l.lower() for l in (pair.get("labels") or [])]
    if "v2" in labels:
        return "v2"
    return "v3"
