// Ambil alamat Uniswap utk Robinhood Chain (4663) dari SDK resmi.
// Setup: npm i @uniswap/sdk-core   lalu: node tools/fetch_addresses.mjs
import { CHAIN_TO_ADDRESSES_MAP } from "@uniswap/sdk-core";

const CHAIN = 4663;
const a = CHAIN_TO_ADDRESSES_MAP[CHAIN];
if (!a) {
  console.error(
    `Chain ${CHAIN} belum ada di versi sdk-core ini. Update: npm i @uniswap/sdk-core@latest\n` +
    `Kalau tetap tidak ada, ambil manual dari halaman "Uniswap Protocol deployment addresses".`
  );
  process.exit(1);
}
console.log("Copy ke .env:\n");
console.log(`SWAP_ROUTER02=${a.swapRouter02Address ?? "(tidak ada — cek docs)"}`);
console.log(`V2_ROUTER=${a.v2RouterAddress ?? "(tidak ada — cek docs)"}`);
console.log("\nFull map:", JSON.stringify(a, null, 2));
