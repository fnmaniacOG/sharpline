"""
markets.py - list the actual odds market types the feed carries for a priced fixture.

Run this if Over/Under bets are not appearing, to confirm the real SuperOddsType labels:
    python3 agent/markets.py
Paste the output and the totals parser can be matched exactly.
"""
from __future__ import annotations
import time
from feed import TxLineClient


def main():
    c = TxLineClient()
    now = time.time() * 1000
    fixtures = sorted(c.fixtures(), key=lambda f: f.get("start") or 0)
    upcoming = [f for f in fixtures if (f.get("start") or 0) >= now]

    for f in (upcoming + fixtures):
        try:
            raw = c._get(f"/api/odds/snapshot/{f['fixture']}")
        except Exception:
            continue
        rows = raw if isinstance(raw, list) else raw.get("odds", [])
        if not rows:
            continue
        seen = {}
        for r in rows:
            t = r.get("SuperOddsType")
            if t not in seen:
                seen[t] = r
        print(f"{f['p1']} v {f['p2']}  ({len(rows)} records)")
        for t, r in seen.items():
            print(f"  {t}: PriceNames={r.get('PriceNames')} "
                  f"MarketParameters={r.get('MarketParameters')} Prices={r.get('Prices')}")
        return
    print("no priced fixture found right now.")


if __name__ == "__main__":
    main()
