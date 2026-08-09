"""Eksekusi buy/sell di Uniswap (Robinhood Chain). Sync — panggil via asyncio.to_thread."""
import threading
import time
from decimal import Decimal, ROUND_DOWN
from web3 import Web3
from eth_account import Account
from . import config, db

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "string"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

ROUTER02_ABI = [
    {"name": "WETH9", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"name": "exactInputSingle", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "tokenIn", "type": "address"},
         {"name": "tokenOut", "type": "address"},
         {"name": "fee", "type": "uint24"},
         {"name": "recipient", "type": "address"},
         {"name": "amountIn", "type": "uint256"},
         {"name": "amountOutMinimum", "type": "uint256"},
         {"name": "sqrtPriceLimitX96", "type": "uint160"}]}],
     "outputs": [{"name": "amountOut", "type": "uint256"}]},
]

V2_ROUTER_ABI = [
    {"name": "WETH", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"name": "swapExactETHForTokensSupportingFeeOnTransferTokens", "type": "function",
     "stateMutability": "payable",
     "inputs": [{"name": "amountOutMin", "type": "uint256"},
                {"name": "path", "type": "address[]"},
                {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"}],
     "outputs": []},
    {"name": "getAmountsOut", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "amountIn", "type": "uint256"},
                {"name": "path", "type": "address[]"}],
     "outputs": [{"name": "amounts", "type": "uint256[]"}]},
    {"name": "swapExactTokensForETHSupportingFeeOnTransferTokens", "type": "function",
     "stateMutability": "nonpayable",
     "inputs": [{"name": "amountIn", "type": "uint256"},
                {"name": "amountOutMin", "type": "uint256"},
                {"name": "path", "type": "address[]"},
                {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"}],
     "outputs": []},
]

POOL_ABI = [
    {"name": "fee", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint24"}]},
]

WETH_ABI = ERC20_ABI + [
    {"name": "withdraw", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "wad", "type": "uint256"}], "outputs": []},
]

MAX_UINT = 2**256 - 1


def _txh(rcpt) -> str:
    h = rcpt.transactionHash.hex()
    return h if h.startswith("0x") else "0x" + h


class Trader:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(config.RPC_URL, request_kwargs={"timeout": 30}))
        self.acct = Account.from_key(config.PRIVATE_KEY)
        self.addr = self.acct.address
        self.router3 = self.w3.eth.contract(
            Web3.to_checksum_address(config.SWAP_ROUTER02), abi=ROUTER02_ABI)
        self.router2 = self.w3.eth.contract(
            Web3.to_checksum_address(config.V2_ROUTER), abi=V2_ROUTER_ABI)
        # validasi: kedua router HARUS punya kontrak di chain ini — kalau tidak,
        # error "Could not transact with/call contract function" baru muncul pas
        # buy (duit mau keluar). Mending gagal jelas di startup.
        cid = self.w3.eth.chain_id
        if cid != config.CHAIN_ID:
            raise SystemExit(f"[trader] RPC nyambung ke chain {cid}, bukan {config.CHAIN_ID} — cek RPC_URL")
        for name, addr in (("SWAP_ROUTER02", self.router3.address),
                           ("V2_ROUTER", self.router2.address)):
            if len(self.w3.eth.get_code(addr)) <= 2:
                raise SystemExit(
                    f"[trader] {name}={addr} TIDAK ADA kontraknya di chain {cid} — "
                    f"alamat salah (mungkin alamat chain lain). Ambil yang benar via "
                    f"'node tools/fetch_addresses.mjs' lalu verifikasi di Blockscout.")
        self.weth_addr = self.router3.functions.WETH9().call()
        self.weth = self.w3.eth.contract(self.weth_addr, abi=WETH_ABI)
        self._nonce_lock = threading.Lock()
        # Satu sell pada satu waktu — cegah race: dua jalur (TP + manual/agent)
        # sama-sama baca balance lalu transferFrom → yang belakangan STF.
        self._sell_lock = threading.Lock()

    # ---------- helpers ----------
    def erc20(self, addr):
        return self.w3.eth.contract(Web3.to_checksum_address(addr), abi=ERC20_ABI)

    def eth_balance(self):
        return self.w3.eth.get_balance(self.addr) / 1e18

    def pool_fee(self, pair_address) -> int:
        try:
            return self.w3.eth.contract(
                Web3.to_checksum_address(pair_address), abi=POOL_ABI
            ).functions.fee().call()
        except Exception:
            return 3000

    def _send(self, fn, value=0, gas_mult=1.3):
        with self._nonce_lock:
            base = {"from": self.addr, "value": value, "chainId": config.CHAIN_ID}
            fn.call(base)  # simulasi dulu
            gas = int(fn.estimate_gas(base) * gas_mult)
            tx = fn.build_transaction({
                **base,
                "gas": gas,
                "gasPrice": int(self.w3.eth.gas_price * 1.2),
                "nonce": self.w3.eth.get_transaction_count(self.addr, "pending"),
            })
            signed = self.acct.sign_transaction(tx)
            h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            rcpt = self.w3.eth.wait_for_transaction_receipt(h, timeout=120)
            if rcpt.status != 1:
                raise RuntimeError(f"tx revert: {h.hex()}")
            return rcpt

    def _ensure_approval(self, token_addr, spender):
        t = self.erc20(token_addr)
        if t.functions.allowance(self.addr, spender).call() < MAX_UINT // 2:
            self._send(t.functions.approve(spender, MAX_UINT))

    # ---------- buy ----------
    def buy(self, ca, pair, source_name="", usd_override=None):
        from . import dexscreener as dx
        ca = Web3.to_checksum_address(ca)
        ptype = dx.pool_type(pair)
        eth_price = dx.eth_usd(pair)
        if eth_price <= 0:
            raise RuntimeError("gagal derive harga ETH dari pair")

        buy_usd = usd_override if usd_override else db.get("buy_usd")
        slippage = db.get("buy_slippage")
        eth_in = buy_usd / eth_price
        wei_in = self.w3.to_wei(eth_in, "ether")

        bal = self.w3.eth.get_balance(self.addr)
        if bal < wei_in * 2:
            raise RuntimeError(f"saldo ETH kurang: {bal/1e18:.6f} ETH")

        token = self.erc20(ca)
        dec = token.functions.decimals().call()
        try:
            sym = token.functions.symbol().call()
        except Exception:
            sym = pair.get("baseToken", {}).get("symbol", "?")

        price_native = float(pair.get("priceNative") or 0)
        expected = (eth_in / price_native) if price_native else 0
        min_out = int(expected * (1 - slippage) * 10**dec) if expected else 0

        before = token.functions.balanceOf(self.addr).call()
        eth_before = self.w3.eth.get_balance(self.addr)

        if ptype == "v2":
            fn = self.router2.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                min_out, [self.weth_addr, ca], self.addr, int(time.time()) + 120)
            rcpt = self._send(fn, value=wei_in)
            router_used, fee = self.router2.address, 0
        else:
            fee = self.pool_fee(pair["pairAddress"])
            fn = self.router3.functions.exactInputSingle((
                self.weth_addr, ca, fee, self.addr, wei_in, min_out, 0))
            rcpt = self._send(fn, value=wei_in)
            router_used = self.router3.address
        buy_tx = _txh(rcpt)

        got_raw = token.functions.balanceOf(self.addr).call() - before
        if got_raw <= 0:
            raise RuntimeError("swap sukses tapi token tidak masuk")
        got = got_raw / 10**dec
        eth_out_real = (eth_before - self.w3.eth.get_balance(self.addr)) / 1e18  # termasuk gas

        self._ensure_approval(ca, router_used)
        honeypot = not self._can_sell(ca, ptype, fee, got_raw)

        return {
            "ca": ca.lower(), "symbol": sym,
            "pair_address": pair["pairAddress"], "chain_slug": pair["chainId"],
            "pool_type": ptype, "fee": fee,
            "entry_price_usd": buy_usd / got,
            "tokens_total": got, "tokens_left": got,
            "eth_spent": eth_out_real if eth_out_real > 0 else eth_in,
            "usd_spent": buy_usd,
            "honeypot": 1 if honeypot else 0,
            "source_name": source_name,
            "buy_tx": buy_tx,
        }

    def _can_sell(self, ca, ptype, fee, amount_raw):
        try:
            if ptype == "v2":
                fn = self.router2.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                    amount_raw, 0, [Web3.to_checksum_address(ca), self.weth_addr],
                    self.addr, int(time.time()) + 120)
            else:
                fn = self.router3.functions.exactInputSingle((
                    Web3.to_checksum_address(ca), self.weth_addr, fee,
                    self.addr, amount_raw, 0, 0))
            fn.call({"from": self.addr})
            return True
        except Exception:
            return False

    # ---------- amount helpers (anti-STF) ----------
    @staticmethod
    def _tokens_to_raw(tokens, dec: int) -> int:
        """Float tokens_left * 10**dec sering OVERSHOOT beberapa wei → transferFrom
        revert 'STF'. Pakai Decimal + ROUND_DOWN biar tidak pernah di atas nilai DB."""
        if tokens is None or tokens <= 0:
            return 0
        try:
            raw = (Decimal(str(tokens)) * (Decimal(10) ** dec)).to_integral_value(rounding=ROUND_DOWN)
            return max(0, int(raw))
        except Exception:
            return max(0, int(float(tokens) * 10 ** dec))

    def _sell_amount_raw(self, pos, fraction):
        """Hitung amountIn yang AMAN (<= balance on-chain).

        Bug lama: `int(tokens_left * fraction * 10**dec)` bisa > balance (float
        precision). Router Uniswap transferFrom exact amount → revert **STF**.
        Manual sell sering "berhasil" karena kebetulan amount kebulatan / timing
        beda; path auto TP (exec_pnl) lebih sering kena karena TANPA clamp.

        Return (amount_raw, decimals, bal_raw).
        """
        ca = Web3.to_checksum_address(pos["ca"])
        token = self.erc20(ca)
        dec = token.functions.decimals().call()
        bal_raw = int(token.functions.balanceOf(self.addr).call())
        if bal_raw <= 0:
            return 0, dec, bal_raw

        frac = float(fraction or 0)
        if frac <= 0:
            return 0, dec, bal_raw

        # Full exit: jual SEMUA saldo on-chain (sumber kebenaran), bukan float DB.
        # Ini juga membersihkan dust sisa partial sell sebelumnya.
        if frac >= 0.999:
            return bal_raw, dec, bal_raw

        # Partial: basis-point dari min(saldo, sisa posisi DB)
        bps = max(1, min(9999, int(round(frac * 10000))))
        pos_left_raw = self._tokens_to_raw(pos.get("tokens_left") or 0, dec)
        base = min(bal_raw, pos_left_raw) if pos_left_raw > 0 else bal_raw
        want = (base * bps) // 10000
        if want <= 0 and base > 0:
            want = max(1, base // 2)
        return min(want, bal_raw), dec, bal_raw

    # ---------- quote (verifikasi TP anti-wick) ----------
    def quote_sell_eth(self, ca, ptype, fee, amount_raw) -> float:
        """Berapa ETH BENERAN yang bakal didapat kalau jual amount_raw sekarang.
        v3: static-call exactInputSingle (butuh allowance — sudah di-approve saat buy).
        v2: getAmountsOut (pure view).
        Ini quote nembus depth pool, bukan harga spot layar."""
        if amount_raw <= 0:
            return 0.0
        ca = Web3.to_checksum_address(ca)
        if ptype == "v2":
            amounts = self.router2.functions.getAmountsOut(
                amount_raw, [ca, self.weth_addr]).call()
            return amounts[-1] / 1e18
        out = self.router3.functions.exactInputSingle((
            ca, self.weth_addr, fee, self.addr, amount_raw, 0, 0)).call({"from": self.addr})
        return out / 1e18

    def exec_pnl(self, pos, fraction) -> float:
        """PnL EKSEKUTABEL: quote hasil jual fraction posisi vs porsi modal ETH-nya.
        Dipakai monitor buat verifikasi TP — biar wick tanpa depth nggak
        men-trigger 'profit' palsu.

        JANGAN raise ke pemanggil: gagal quote (STF/RPC/pool) → -1.0 supaya
        monitor menahan TP (mirip pool tipis), BUKAN spam 'Gagal eksekusi sell'.
        """
        try:
            amount_raw, _, _ = self._sell_amount_raw(pos, fraction)
            if amount_raw <= 0:
                return -1.0
            eth_quote = self.quote_sell_eth(
                pos["ca"], pos["pool_type"], pos["fee"], amount_raw)
            cost = pos["eth_spent"] * ((pos["tokens_left"] * fraction) / pos["tokens_total"]) \
                if pos["tokens_total"] else 0
            return (eth_quote / cost - 1) if cost > 0 and eth_quote > 0 else -1.0
        except Exception:
            return -1.0

    # ---------- sell ----------
    def sell(self, pos, fraction, cur_price_native):
        """Jual `fraction` dari tokens_left.

        Return (tokens_sold, eth_realized, gas_eth, swap_tx):
        eth_realized = ETH ASLI yang masuk wallet (balance delta, bukan estimasi
        dari harga spot) — supaya PnL yang dilaporkan jujur walau price impact
        besar di pool tipis. gas_eth = total gas yang kepakai selama proses sell.
        """
        with self._sell_lock:
            return self._sell_locked(pos, fraction, cur_price_native)

    def _sell_locked(self, pos, fraction, cur_price_native):
        ca = Web3.to_checksum_address(pos["ca"])
        amount_raw, dec, bal_raw = self._sell_amount_raw(pos, fraction)
        if amount_raw <= 0:
            return 0.0, 0.0, 0.0, None

        # Re-read balance di dalam lock (anti race dengan jalur sell lain)
        token = self.erc20(ca)
        bal_raw = int(token.functions.balanceOf(self.addr).call())
        amount_raw = min(amount_raw, bal_raw)
        if amount_raw <= 0:
            return 0.0, 0.0, 0.0, None

        slippage = db.get("sell_slippage")
        # min_out dari harga layar; di pool tipis/volatil sering terlalu optimis
        # → panic fallback min_out=1 di bawah. Clamp min_out biar nggak overflow.
        expected_eth = (amount_raw / 10 ** dec) * float(cur_price_native or 0)
        min_out = 0
        if expected_eth > 0:
            min_out = int(self.w3.to_wei(expected_eth * (1 - slippage), "ether"))
            min_out = max(1, min_out)

        eth_before = self.w3.eth.get_balance(self.addr)
        weth_before = self.weth.functions.balanceOf(self.addr).call()
        gas_wei = 0
        swap_tx = None

        def send(fn, value=0, is_swap=False):
            nonlocal gas_wei, swap_tx
            rcpt = self._send(fn, value=value)
            gas_wei += rcpt.gasUsed * rcpt.effectiveGasPrice
            if is_swap:
                swap_tx = _txh(rcpt)
            return rcpt

        def refresh_amount():
            """Sebelum panic retry: clamp ulang ke saldo terkini."""
            nonlocal amount_raw
            bal = int(token.functions.balanceOf(self.addr).call())
            amount_raw = min(amount_raw, bal)
            return amount_raw > 0

        if pos["pool_type"] == "v2":
            self._ensure_approval(ca, self.router2.address)

            def mk(mo):
                return self.router2.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                    amount_raw, mo, [ca, self.weth_addr], self.addr, int(time.time()) + 120)

            try:
                send(mk(min_out), is_swap=True)
            except Exception as first_err:
                if not refresh_amount():
                    raise first_err
                try:
                    send(mk(1), is_swap=True)  # panic exit
                except Exception as panic_err:
                    raise RuntimeError(
                        f"sell v2 gagal (min_out & panic). pertama: {first_err}; panic: {panic_err}"
                    ) from panic_err
        else:
            self._ensure_approval(ca, self.router3.address)

            def mk(mo):
                return self.router3.functions.exactInputSingle((
                    ca, self.weth_addr, int(pos["fee"] or 3000), self.addr, amount_raw, mo, 0))

            try:
                send(mk(min_out), is_swap=True)
            except Exception as first_err:
                if not refresh_amount():
                    raise first_err
                try:
                    send(mk(1), is_swap=True)
                except Exception as panic_err:
                    raise RuntimeError(
                        f"sell v3 gagal (min_out & panic). pertama: {first_err}; panic: {panic_err}"
                    ) from panic_err
            wbal = self.weth.functions.balanceOf(self.addr).call()
            if wbal > weth_before:
                send(self.weth.functions.withdraw(wbal - weth_before))

        eth_after = self.w3.eth.get_balance(self.addr)
        # realized = delta saldo + gas yang kepotong selama sell (gas dilaporkan terpisah)
        eth_realized = max((eth_after - eth_before + gas_wei) / 1e18, 0.0)
        return amount_raw / 10 ** dec, eth_realized, gas_wei / 1e18, swap_tx
