/**
 * activate.ts - one-time TxLINE free-tier setup on Solana devnet.
 *
 * Flow:  guest JWT  ->  on-chain subscribe (free tier, no payment)  ->  activate  ->  save API token
 *
 * SETUP (run on your machine; needs internet, will not work in a no-egress sandbox):
 *   1. From the sharpline/ folder:  npm install
 *   2. Make a devnet wallet and fund it for fees:
 *        solana-keygen new -o wallet.json
 *        solana airdrop 2 $(solana address -k wallet.json) --url devnet
 *      (wallet.json sits in sharpline/. Set WALLET_PATH to change it.)
 *   3. Download the Devnet IDL JSON from
 *        https://txline-docs.txodds.com/documentation/programs/devnet
 *      and save it as  sharpline/idl.json
 *   4. Run:  npx ts-node auth/activate.ts
 *
 * On success it writes TXLINE_API_TOKEN (and the base URL) to sharpline/.env, which the
 * Python agent reads. If the on-chain subscribe errors on a specific account, the TxODDS
 * hackathon Discord (discord.gg/txodds) can confirm the exact free-tier account setup.
 */

import fs from "fs";
import path from "path";
import axios from "axios";
import nacl from "tweetnacl";
import * as anchor from "@coral-xyz/anchor";
import { Connection, Keypair, PublicKey, SystemProgram } from "@solana/web3.js";
import {
  getAssociatedTokenAddressSync,
  createAssociatedTokenAccountIdempotent,
  TOKEN_2022_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
} from "@solana/spl-token";

// ---- devnet config (free World Cup tier) ----
const RPC_URL = process.env.RPC_URL || "https://api.devnet.solana.com";
const API_BASE = process.env.TXLINE_BASE || "https://txline-dev.txodds.com"; // mainnet: https://txline.txodds.com
const PROGRAM_ID = new PublicKey("6pW64gN1s2uqjHkn1unFeEjAwJkPGHoppGvS715wyP2J");
const TXL_MINT = new PublicKey("4Zao8ocPhmMgq7PdsYWyxvqySMGx7xb9cMftPMkEokRG");
const SERVICE_LEVEL_ID = 1;          // 1 = free (60s delay) | 12 = free (real-time)
const DURATION_WEEKS = 4;            // free tiers subscribe in 4-week blocks
const SELECTED_LEAGUES: number[] = []; // empty = standard World Cup bundle

const ROOT = process.cwd(); // run this from the sharpline/ folder
const WALLET_PATH = process.env.WALLET_PATH || path.resolve(ROOT, "wallet.json");
const IDL_PATH = process.env.IDL_PATH || path.resolve(ROOT, "idl.json");
const ENV_PATH = path.resolve(ROOT, ".env");

function loadWallet(): Keypair {
  const secret = JSON.parse(fs.readFileSync(WALLET_PATH, "utf8"));
  return Keypair.fromSecretKey(Uint8Array.from(secret));
}

async function main() {
  const wallet = loadWallet();
  console.log("wallet:", wallet.publicKey.toBase58());

  const connection = new Connection(RPC_URL, "confirmed");
  const provider = new anchor.AnchorProvider(connection, new anchor.Wallet(wallet), {
    commitment: "confirmed",
  });
  anchor.setProvider(provider);

  const idl = JSON.parse(fs.readFileSync(IDL_PATH, "utf8"));
  idl.address = idl.address || PROGRAM_ID.toBase58();
  // anchor >= 0.30 resolves the program id from idl.address. On older anchor use:
  //   new anchor.Program(idl, PROGRAM_ID, provider)
  const program = new anchor.Program(idl as anchor.Idl, provider);

  // PDAs (seeds documented in Program Addresses)
  const [pricingMatrixPda] = PublicKey.findProgramAddressSync(
    [Buffer.from("pricing_matrix")], PROGRAM_ID);
  const [tokenTreasuryPda] = PublicKey.findProgramAddressSync(
    [Buffer.from("token_treasury_v2")], PROGRAM_ID);
  const tokenTreasuryVault = getAssociatedTokenAddressSync(
    TXL_MINT, tokenTreasuryPda, true, TOKEN_2022_PROGRAM_ID);
  const userTokenAccount = getAssociatedTokenAddressSync(
    TXL_MINT, wallet.publicKey, false, TOKEN_2022_PROGRAM_ID);

  // 1. guest session JWT
  const authRes = await axios.post(`${API_BASE}/auth/guest/start`);
  const jwt = authRes.data.token;
  console.log("guest JWT acquired");

  // the subscribe ix references your TXL token account; create it (empty) if missing
  let ataInfo = await connection.getAccountInfo(userTokenAccount);
  if (!ataInfo) {
    console.log("creating TXL token account...");
    await createAssociatedTokenAccountIdempotent(
      connection, wallet, TXL_MINT, wallet.publicKey, {}, TOKEN_2022_PROGRAM_ID);
    ataInfo = await connection.getAccountInfo(userTokenAccount);
  }
  console.log("userTokenAccount:", userTokenAccount.toBase58(),
              ataInfo ? "(ready)" : "(still missing)");

  // 2. subscribe on-chain (free tier: registers the subscription, transfers no tokens)
  let txSig: string;
  try {
    txSig = await program.methods
      .subscribe(SERVICE_LEVEL_ID, DURATION_WEEKS)
      .accounts({
        user: wallet.publicKey,
        pricingMatrix: pricingMatrixPda,
        tokenMint: TXL_MINT,
        userTokenAccount,
        tokenTreasuryVault,
        tokenTreasuryPda,
        tokenProgram: TOKEN_2022_PROGRAM_ID,
        associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID,
        systemProgram: SystemProgram.programId,
      })
      .rpc();
  } catch (err: any) {
    // pull the real program logs out of the simulation error
    const logs = err?.logs || (typeof err?.getLogs === "function" ? await err.getLogs(connection) : null);
    console.error("subscribe failed. program logs:");
    console.error(logs ? logs.join("\n") : (err?.message || err));
    throw err;
  }
  await connection.confirmTransaction(txSig, "confirmed");
  console.log("subscribed on-chain:", txSig);

  // 3. activate API access by signing the subscription
  const messageString = `${txSig}:${SELECTED_LEAGUES.join(",")}:${jwt}`;
  const sigBytes = nacl.sign.detached(new TextEncoder().encode(messageString), wallet.secretKey);
  const walletSignature = Buffer.from(sigBytes).toString("base64");

  const actRes = await axios.post(
    `${API_BASE}/api/token/activate`,
    { txSig, walletSignature, leagues: SELECTED_LEAGUES },
    { headers: { Authorization: `Bearer ${jwt}` } });
  const apiToken = actRes.data.token || actRes.data;
  console.log("API token activated");

  // 4. save for the Python agent
  fs.writeFileSync(ENV_PATH, `TXLINE_API_TOKEN=${apiToken}\nTXLINE_BASE=${API_BASE}\n`);
  console.log("wrote token to", ENV_PATH);
}

main().catch((e) => {
  console.error("activation failed:", e?.response?.data || e?.message || e);
  process.exit(1);
});
