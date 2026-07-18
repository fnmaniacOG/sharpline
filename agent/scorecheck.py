"""
scorecheck.py - for each fixture, show the RAW response from every score endpoint TxLINE
offers, so we can tell "the feed has no final score" apart from "my parser is dropping it".

Run:  python3 agent/scorecheck.py
"""
from __future__ import annotations
import json
import time
from feed import TxLineClient, parse_score_event


def main():
    c = TxLineClient()
    now = time.time() * 1000
    fixtures = sorted(c.fixtures(), key=lambda f: f.get("start") or 0)
    print(f"{len(fixtures)} fixtures.\n")

    endpoints = [
        ("snapshot",   lambda fid: f"/api/scores/snapshot/{fid}"),
        ("updates",    lambda fid: f"/api/scores/updates/{fid}"),
        ("historical", lambda fid: f"/api/scores/historical/{fid}"),
    ]

    for f in fixtures:
        fid = f["fixture"]
        start = f.get("start") or 0
        h = (start - now) / 3600000.0
        when = f"started {-h:.0f}h ago" if h < 0 else f"in {h:.0f}h"
        print(f"=== {f['p1']} v {f['p2']}  ({when}, fixtureId {fid}) ===")
        for name, path in endpoints:
            try:
                raw = c._get(path(fid))
                rows = raw if isinstance(raw, list) else (raw.get("scores", raw) if isinstance(raw, dict) else [])
                n = len(rows) if isinstance(rows, list) else "?"
                if isinstance(rows, list) and rows:
                    last = rows[-1]
                    parsed = parse_score_event(last) if isinstance(last, dict) else None
                    p = f"parsed: {parsed.phase} {parsed.home}-{parsed.away} ended={parsed.ended}" if parsed else "parsed: NONE"
                    keys = list(last.keys())[:12] if isinstance(last, dict) else last
                    print(f"  {name:<11} rows={n} | {p}")
                    print(f"              last-record keys: {keys}")
                else:
                    print(f"  {name:<11} rows={n} (empty)")
            except Exception as e:
                print(f"  {name:<11} ERROR: {e}")
        print()


if __name__ == "__main__":
    main()
