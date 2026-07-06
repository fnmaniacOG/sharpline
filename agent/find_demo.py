"""
find_demo.py - scan upcoming fixtures and report which ones the agent would BET right now.

Use this before recording to pick a fixture that actually produces a disciplined bet (a
close matchup), instead of a heavy favorite where the agent correctly passes.

Run:  python3 agent/find_demo.py
"""
from __future__ import annotations
import time

from feed import TxLineClient, to_agent_update
from model import model_probs, anchor_to_market, devig
from agent import Agent


def main():
    c = TxLineClient()
    now = time.time() * 1000
    fixtures = c.fixtures()
    upcoming = sorted((f for f in fixtures if (f.get("start") or 0) >= now),
                      key=lambda f: f["start"] or 0)

    print(f"scanning {len(upcoming)} upcoming fixtures for a demo-worthy bet...\n")
    bettable = []
    for f in upcoming:
        fid = f["fixture"]
        try:
            snaps = c.odds_snapshot(fid)
        except Exception:
            continue
        if not snaps:
            continue
        snap = snaps[-1]
        raw = model_probs(f["p1"], f["p2"], neutral=(f.get("competition_id") == 72))
        anchored = anchor_to_market(raw, devig(snap.outcomes), 0.30)
        a = Agent(bankroll=1000)
        decisions = a.process(to_agent_update(snap, anchored))
        mkt = "  ".join(f"{k} {v:.2f}" for k, v in snap.outcomes.items())
        if decisions:
            d = decisions[0]
            print(f"BET  {fid}  {f['p1']} v {f['p2']}")
            print(f"     market {mkt}")
            print(f"     -> {d['outcome']} ${d['stake']} @ {d['decimal']:.2f} "
                  f"(edge {d['edge']:.1%}, conf {d['confidence']:.2f})\n")
            bettable.append(fid)
        else:
            print(f"pass {fid}  {f['p1']} v {f['p2']}   ({mkt})")

    print()
    if bettable:
        print("record one of these (a real, disciplined bet):")
        for fid in bettable:
            print(f"  python3 agent/run_live.py --fixture {fid} --log agent/decisions.jsonl")
    else:
        print("no qualifying bet right now. either wait for closer lines, or record a PASS")
        print("and narrate the discipline (the agent standing down IS a selling point).")


if __name__ == "__main__":
    main()
