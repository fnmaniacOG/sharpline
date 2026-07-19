"""
fix_session.py - correct the local paper session in place, without resetting anything.

It re-derives bankroll, realized P&L, the win-loss record, and the SETTLE log lines from the
BET log entries plus the verified results (backfill_elo). Every bet and every open position is
kept. It only fixes settlements that were wrong because a game was missing from the results
(e.g. France v England, which the bad devnet score settled as a draw).

Nothing here touches the blockchain. The on-chain memos are permanent and are left untouched.

Run with the agent STOPPED (so it does not overwrite the file):
    # Stop the agent on the dashboard first
    python3 agent/backfill_elo.py      # make sure ratings + results are current
    python3 agent/fix_session.py
    # then Start the agent again
"""
from __future__ import annotations
import json
import os
import re

import chain
from run_all import known_result, winners_from_score

SESSION = os.path.join(os.path.dirname(__file__), "..", "dashboard", "session.json")
BET_RE = re.compile(r"^(.+?) v (.+?) \[(.+?)\]: ([A-Z]+) \$([\d.]+) @ ([\d.]+)")


def main():
    with open(SESSION) as f:
        s = json.load(f)

    onchain = chain.available()
    print(f"on-chain settlement memos: {chain.status()}")

    # positions still open (e.g. the final): match them so we do not settle them
    open_sig = {(p["market"], p["outcome"], round(p["stake"], 2), round(p["decimal"], 2))
                for p in s.get("positions", [])}

    bankroll = 1000.0
    realized = 0.0
    wins = losses = 0
    new_logs = []
    fixed = 0

    for entry in s.get("logs", []):
        if entry.get("kind") != "BET":
            continue                                 # drop old SETTLE lines; we rebuild them
        m = BET_RE.match(entry.get("desc", ""))
        if not m:
            new_logs.append(entry)
            continue
        home, away, market, outcome = m.group(1), m.group(2), m.group(3), m.group(4)
        stake, dec = float(m.group(5)), float(m.group(6))

        new_logs.append(entry)                        # keep the BET line
        bankroll -= stake                             # stake was deducted when the bet opened

        sig = (market, outcome, round(stake, 2), round(dec, 2))
        res = known_result(home, away)
        if sig in open_sig or res is None:
            continue                                  # still open / no result yet -> leave it

        win = winners_from_score(*res).get(market)
        if win == "PUSH":
            bankroll += stake; pnl, tag = 0.0, "PUSH (refund)"
        elif outcome == win:
            bankroll += stake * dec; pnl, tag = round(stake * (dec - 1), 2), "WON"; wins += 1
        else:
            pnl, tag = round(-stake, 2), "lost"; losses += 1
        realized += pnl
        desc = f"{home} v {away} [{market}]: {win} · {tag} {pnl:+.2f}"
        sig = chain.post_memo(f"SharpLine SETTLE | {desc}") if onchain else None   # its OWN tx
        new_logs.append({"kind": "SETTLE", "sig": sig, "desc": desc})
        print(f"  settled {desc}" + (f"  ->  {sig}" if sig else "  (not posted)"))
        fixed += 1

    s["bankroll"] = round(bankroll, 2)
    s["realized"] = round(realized, 2)
    s["wins"], s["losses"] = wins, losses
    s["logs"] = new_logs
    with open(SESSION, "w") as f:
        json.dump(s, f)

    print(f"session corrected: bankroll ${s['bankroll']} | realized {s['realized']:+.2f} | "
          f"record {wins}-{losses} | {fixed} settlements recomputed from verified results")


if __name__ == "__main__":
    main()
