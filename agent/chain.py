"""
chain.py - post a decision to Solana devnet as a Memo transaction (from Python).

This lets the agent write its own on-chain audit record live, so the dashboard can show a
real, clickable explorer link for each decision. It is a MEMO only: a signed text record.
It does not move tokens; the paper bankroll and stakes are simulated. The wallet pays only
the tiny transaction fee.

Needs:  pip install solana        (pulls in solders)
        a funded devnet wallet.json (the same one auth used)

Graceful: if solana is not installed or the post fails, returns None and the agent keeps
running (the dashboard just shows the memo without a link).
"""
from __future__ import annotations
import json
import os
from functools import lru_cache
from typing import Optional

MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
RPC = os.environ.get("RPC_URL", "https://api.devnet.solana.com")
WALLET = os.path.join(os.path.dirname(__file__), "..", "wallet.json")


@lru_cache(maxsize=1)
def _wallet_and_client():
    from solders.keypair import Keypair
    from solana.rpc.api import Client
    with open(WALLET) as f:
        secret = json.load(f)
    return Keypair.from_bytes(bytes(secret)), Client(RPC)


def available() -> bool:
    try:
        import solana  # noqa: F401
        import solders  # noqa: F401
        return os.path.exists(WALLET)
    except Exception:
        return False


def post_memo(text: str) -> Optional[str]:
    """Post `text` as a devnet memo. Returns the transaction signature, or None on failure."""
    try:
        from solders.pubkey import Pubkey
        from solders.instruction import Instruction, AccountMeta
        from solders.message import Message
        from solders.transaction import Transaction

        kp, client = _wallet_and_client()
        ix = Instruction(
            Pubkey.from_string(MEMO_PROGRAM),
            text[:560].encode("utf-8"),
            [AccountMeta(kp.pubkey(), True, False)],
        )
        blockhash = client.get_latest_blockhash().value.blockhash
        msg = Message.new_with_blockhash([ix], kp.pubkey(), blockhash)
        tx = Transaction([kp], msg, blockhash)
        return str(client.send_transaction(tx).value)
    except Exception as e:
        print(f"[chain] memo post skipped: {e}")
        return None


if __name__ == "__main__":
    print("solana available:", available())
    sig = post_memo("sharpline:test memo")
    if sig:
        print("posted:", sig)
        print(f"https://explorer.solana.com/tx/{sig}?cluster=devnet")
