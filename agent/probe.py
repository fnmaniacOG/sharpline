"""
probe.py - one-off: dump REAL TxLINE records so we can lock the odds parser in feed.py.

Run on your machine:
    pip install requests        # if needed
    python3 agent/probe.py

The first fixture in the list may be unplayed (no odds/scores yet). So this scans
already-started fixtures (most recent first), tries several odds + scores endpoints,
and prints the first NON-EMPTY raw record of each so we see real field names.
Paste the output back.
"""
from __future__ import annotations
import json
import time
from feed import TxLineClient


def first_n(obj, n=2, width=1600):
    rows = obj
    if isinstance(obj, dict):
        for k in ("fixtures", "odds", "scores", "data", "results"):
            if isinstance(obj.get(k), list):
                rows = obj[k]; break
    head = rows[:n] if isinstance(rows, list) else rows
    return json.dumps(head, indent=2)[:width], (rows if isinstance(rows, list) else [])


def try_get(c, path):
    try:
        return c._get(path), None
    except Exception as e:
        return None, str(e)


def main():
    c = TxLineClient()
    print("base:", c.base, "| jwt ok:", bool(c.jwt), "| api token:", bool(c.api_token), "\n")
    now = time.time() * 1000

    raw = c._get("/api/fixtures/snapshot")
    fixtures = raw.get("fixtures", raw) if isinstance(raw, dict) else raw
    print(f"total fixtures: {len(fixtures)}")

    started = [f for f in fixtures if isinstance(f, dict) and (f.get("StartTime") or 0) < now]
    upcoming = [f for f in fixtures if isinstance(f, dict) and (f.get("StartTime") or 0) >= now]
    started.sort(key=lambda f: f.get("StartTime", 0), reverse=True)   # most recent first
    upcoming.sort(key=lambda f: f.get("StartTime", 0))                # soonest first
    print(f"started: {len(started)} | upcoming: {len(upcoming)}")

    def tag(f):
        return f'{f.get("Participant1")} v {f.get("Participant2")} [{f.get("Competition")}]'

    # ODDS: pre-match odds live on upcoming fixtures (soonest first), then started.
    got_odds = False
    for f in (upcoming + started):
        fid = f.get("FixtureId")
        d, err = try_get(c, f"/api/odds/snapshot/{fid}")
        if err:
            print(f"odds/snapshot/{fid} -> err: {err}")
            continue
        dump, rows = first_n(d)
        if rows:
            print(f"=== ODDS from /api/odds/snapshot/{fid}  ({tag(f)}) ===")
            print(dump, "\n")
            got_odds = True
            break
        else:
            print(f"odds/snapshot/{fid} -> empty ({tag(f)})")

    # SCORES: started fixtures carry live/finished score state.
    got_scores = False
    for f in started:
        fid = f.get("FixtureId")
        d, err = try_get(c, f"/api/scores/snapshot/{fid}")
        if err:
            print(f"scores/snapshot/{fid} -> err: {err}")
            continue
        dump, rows = first_n(d)
        if rows:
            print(f"=== SCORES from /api/scores/snapshot/{fid}  ({tag(f)}) ===")
            print(dump[:900], "\n")
            got_scores = True
            break

    if not got_odds:
        print("no non-empty odds found across candidates.")
    if not got_scores:
        print("no non-empty scores found across candidates.")


if __name__ == "__main__":
    main()
