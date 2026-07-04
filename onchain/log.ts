/**
 * log.ts - write the agent's decisions to Solana devnet as Memo transactions.
 *
 * Each bet and settlement becomes one on-chain memo signed by the agent wallet: a public,
 * timestamped, tamper-evident audit trail. Anyone can verify a decision by its tx signature
 * on the devnet explorer. No custom program required (uses the SPL Memo program).
 *
 * The Python agent appends decisions to agent/decisions.jsonl (run_live.py --log). This posts
 * every not-yet-logged line and appends {line, signature} to onchain/logged.jsonl so re-runs
 * only post new records.
 *
 * Run (needs a funded devnet wallet.json, same one activate.ts used):
 *   npx ts-node onchain/log.ts
 *   npx ts-node onchain/log.ts --file ../agent/decisions.jsonl
 */

import fs from "fs";
import path from "path";
import {
  Connection, Keypair, PublicKey, Transaction, TransactionInstruction, sendAndConfirmTransaction,
} from "@solana/web3.js";

const RPC_URL = process.env.RPC_URL || "https://api.devnet.solana.com";
const MEMO_PROGRAM_ID = new PublicKey("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr");

const ROOT = process.cwd(); // run from the sharpline/ folder
const WALLET_PATH = process.env.WALLET_PATH || path.resolve(ROOT, "wallet.json");

function arg(name: string, fallback: string): string {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const DECISIONS = path.resolve(ROOT, arg("--file", "agent/decisions.jsonl"));
const LOGGED = path.resolve(ROOT, "onchain/logged.jsonl");

function loadWallet(): Keypair {
  const secret = JSON.parse(fs.readFileSync(WALLET_PATH, "utf8"));
  return Keypair.fromSecretKey(Uint8Array.from(secret));
}

function readLines(file: string): string[] {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8").split("\n").map((l) => l.trim()).filter(Boolean);
}

async function memoTx(conn: Connection, wallet: Keypair, memo: string): Promise<string> {
  const ix = new TransactionInstruction({
    keys: [{ pubkey: wallet.publicKey, isSigner: true, isWritable: false }],
    programId: MEMO_PROGRAM_ID,
    data: Buffer.from(memo, "utf8"),
  });
  const tx = new Transaction().add(ix);
  return sendAndConfirmTransaction(conn, tx, [wallet], { commitment: "confirmed" });
}

async function main() {
  const wallet = loadWallet();
  const conn = new Connection(RPC_URL, "confirmed");

  const decisions = readLines(DECISIONS);
  const alreadyLogged = readLines(LOGGED).length; // one logged line per posted decision
  const pending = decisions.slice(alreadyLogged);

  if (pending.length === 0) {
    console.log(`nothing new to log (${decisions.length} decisions, all posted).`);
    return;
  }
  console.log(`posting ${pending.length} decision(s) to devnet as memos...\n`);

  for (const line of pending) {
    // memos must fit a transaction; keep them compact (they already are)
    const memo = `sharpline:${line}`.slice(0, 560);
    const sig = await memoTx(conn, wallet, memo);
    fs.appendFileSync(LOGGED, JSON.stringify({ line, signature: sig }) + "\n");
    console.log(`  logged: ${sig}`);
    console.log(`    https://explorer.solana.com/tx/${sig}?cluster=devnet`);
  }
  console.log(`\ndone. ${pending.length} decision(s) on-chain.`);
}

main().catch((e) => {
  console.error("logging failed:", e?.message || e);
  process.exit(1);
});
