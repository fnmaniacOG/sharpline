"""
chain.py - post a decision to Solana devnet as a Memo transaction (from Python).

Builds and signs the transaction with `solders` and sends it over plain JSON-RPC with
`requests`. It deliberately does NOT use solana-py, whose module layout varies by version;
solders + requests are all that is needed and are far more stable.

It is a MEMO only: a signed text record. It moves no tokens; the paper bankroll and stakes
are simulated. The wallet pays only the tiny transaction fee.

Needs:  pip install solders requests
        a funded devnet wallet.json (the same one auth used)

Graceful: on any failure it prints the reason and returns None; the agent keeps running.
"""
from __future__ import annotations
import base64
import json
import os
from functools import lru_cache
from typing import Optional

MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
RPC = os.environ.get("RPC_URL", "https://api.devnet.solana.com")
WALLET = os.path.join(os.path.dirname(__file__), "..", "wallet.json")


@lru_cache(maxsize=1)
def _keypair():
    from solders.keypair import Keypair
    with open(WALLET) as f:
        secret = json.load(f)
    return Keypair.from_bytes(bytes(secret))


def available() -> bool:
    try:
        import solders  # noqa: F401
        import requests  # noqa: F401
        return os.path.exists(WALLET)
    except Exception:
        return False


def status() -> str:
    """Human-readable reason on-chain posting is on or off."""
    try:
        import solders  # noqa: F401
        import requests  # noqa: F401
    except Exception as e:
        return f"off (run: python3 -m pip install solders requests  |  {type(e).__name__})"
    if not os.path.exists(WALLET):
        return "off (wallet.json not found next to the agent)"
    return "ON (devnet memos)"


def post_memo(text: str) -> Optional[str]:
    """Post `text` as a devnet memo. Returns the transaction signature, or None on failure."""
    try:
        import requests
        from solders.pubkey import Pubkey
        from solders.instruction import Instruction, AccountMeta
        from solders.hash import Hash
        from solders.transaction import Transaction

        kp = _keypair()
        rpc_headers = {"Content-Type": "application/json"}

        bh_resp = requests.post(RPC, headers=rpc_headers, timeout=30, json={
            "jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash",
            "params": [{"commitment": "confirmed"}]}).json()
        blockhash = Hash.from_string(bh_resp["result"]["value"]["blockhash"])

        ix = Instruction(
            Pubkey.from_string(MEMO_PROGRAM),
            text[:560].encode("utf-8"),
            [AccountMeta(kp.pubkey(), True, False)],
        )
        tx = Transaction.new_signed_with_payer([ix], kp.pubkey(), [kp], blockhash)
        raw = base64.b64encode(bytes(tx)).decode()

        send = requests.post(RPC, headers=rpc_headers, timeout=30, json={
            "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [raw, {"encoding": "base64", "preflightCommitment": "confirmed"}]}).json()
        if "result" in send:
            return send["result"]
        print("[chain] send error:", send.get("error"))
        return None
    except Exception as e:
        print(f"[chain] memo post skipped: {e}")
        return None


if __name__ == "__main__":
    print("status:", status())
    sig = post_memo("sharpline:test memo")
    if sig:
        print("POSTED:", sig)
        print(f"https://explorer.solana.com/tx/{sig}?cluster=devnet")
    else:
        print("NOT posted. Fix per the reason above (pip install solders requests, or fund "
              "wallet.json: solana airdrop 1 $(solana address -k wallet.json) --url devnet).")
