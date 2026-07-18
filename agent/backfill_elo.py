"""
backfill_elo.py - learn the ratings from finished World Cup games.

Starts from the pristine base table, replays every finished game in order (group stage
first, then completed knockouts), applies the World-Football-Elo result update, and persists
the learned ratings to elo_overrides.json. Deterministic: re-running gives the same result,
it does not compound.

Sources of finished games:
  1. GROUP_RESULTS below - real matchday-1 group scores (from the World Cup simulator).
  2. TxLINE - any finished fixtures the devnet feed exposes (knockouts as they complete).

Run:
    python3 agent/backfill_elo.py            # seed results only (no network needed)
    python3 agent/backfill_elo.py --txline   # also pull finished games from TxLINE
"""

from __future__ import annotations
import argparse

from model import ELO, BASE_ELO
from ratings import learn_from_results, K_WORLD_CUP, games_learned_count, reset_ledger

# Real results, neutral venues, (home, away, goals_home, goals_away). Penalty-shootout
# games are recorded as draws (their regulation score), which is correct for Elo.
GROUP_RESULTS = [
    # Group A
    ("Mexico", "Czechia", 3, 0), ("Mexico", "South Korea", 1, 0), ("Mexico", "South Africa", 2, 0),
    ("Czechia", "South Korea", 1, 2), ("Czechia", "South Africa", 1, 1), ("South Korea", "South Africa", 0, 1),
    # Group B
    ("Switzerland", "Canada", 2, 1), ("Switzerland", "Bosnia-Herzegovina", 4, 1), ("Switzerland", "Qatar", 1, 1),
    ("Canada", "Bosnia-Herzegovina", 1, 1), ("Canada", "Qatar", 6, 0), ("Bosnia-Herzegovina", "Qatar", 3, 1),
    # Group C
    ("Brazil", "Morocco", 1, 1), ("Brazil", "Scotland", 3, 0), ("Brazil", "Haiti", 3, 0),
    ("Morocco", "Scotland", 1, 0), ("Morocco", "Haiti", 4, 2), ("Scotland", "Haiti", 1, 0),
    # Group D
    ("United States", "Türkiye", 2, 3), ("United States", "Australia", 2, 0), ("United States", "Paraguay", 4, 1),
    ("Türkiye", "Australia", 0, 2), ("Türkiye", "Paraguay", 0, 1), ("Australia", "Paraguay", 0, 0),
    # Group E
    ("Germany", "Côte d'Ivoire", 2, 1), ("Germany", "Ecuador", 1, 2), ("Germany", "Curaçao", 7, 1),
    ("Côte d'Ivoire", "Ecuador", 1, 0), ("Côte d'Ivoire", "Curaçao", 2, 0), ("Ecuador", "Curaçao", 0, 0),
    # Group F
    ("Netherlands", "Japan", 2, 2), ("Netherlands", "Sweden", 5, 1), ("Netherlands", "Tunisia", 3, 1),
    ("Japan", "Sweden", 1, 1), ("Japan", "Tunisia", 4, 0), ("Sweden", "Tunisia", 5, 1),
    # Group G
    ("Belgium", "Egypt", 1, 1), ("Belgium", "Iran", 0, 0), ("Belgium", "New Zealand", 5, 1),
    ("Egypt", "Iran", 1, 1), ("Egypt", "New Zealand", 3, 1), ("Iran", "New Zealand", 2, 2),
    # Group H
    ("Spain", "Uruguay", 1, 0), ("Spain", "Cabo Verde", 0, 0), ("Spain", "Saudi Arabia", 4, 0),
    ("Uruguay", "Cabo Verde", 2, 2), ("Uruguay", "Saudi Arabia", 1, 1), ("Cabo Verde", "Saudi Arabia", 0, 0),
    # Group I
    ("France", "Norway", 4, 1), ("France", "Senegal", 3, 1), ("France", "Iraq", 3, 0),
    ("Norway", "Senegal", 3, 2), ("Norway", "Iraq", 4, 1), ("Senegal", "Iraq", 5, 0),
    # Group J
    ("Argentina", "Austria", 2, 0), ("Argentina", "Algeria", 3, 0), ("Argentina", "Jordan", 3, 1),
    ("Austria", "Algeria", 3, 3), ("Austria", "Jordan", 3, 1), ("Algeria", "Jordan", 2, 1),
    # Group K
    ("Portugal", "Colombia", 0, 0), ("Portugal", "Congo DR", 1, 1), ("Portugal", "Uzbekistan", 5, 0),
    ("Colombia", "Congo DR", 1, 0), ("Colombia", "Uzbekistan", 3, 1), ("Congo DR", "Uzbekistan", 3, 1),
    # Group L
    ("England", "Croatia", 4, 2), ("England", "Ghana", 0, 0), ("England", "Panama", 2, 0),
    ("Croatia", "Ghana", 2, 1), ("Croatia", "Panama", 1, 0), ("Ghana", "Panama", 1, 0),
]

KNOCKOUT_RESULTS = [
    # Round of 32 (penalty games shown as their 1-1 / 0-0 draw)
    ("Germany", "Paraguay", 1, 1), ("France", "Sweden", 3, 0), ("South Africa", "Canada", 0, 1),
    ("Netherlands", "Morocco", 1, 1), ("Portugal", "Croatia", 2, 1), ("Spain", "Austria", 3, 0),
    ("United States", "Bosnia-Herzegovina", 2, 0), ("Belgium", "Senegal", 3, 2),
    ("Brazil", "Japan", 2, 1), ("Côte d'Ivoire", "Norway", 1, 2), ("Mexico", "Ecuador", 2, 0),
    ("England", "Congo DR", 2, 1), ("Argentina", "Cabo Verde", 3, 2), ("Australia", "Egypt", 1, 1),
    ("Switzerland", "Algeria", 2, 0), ("Colombia", "Ghana", 1, 0),
    # Round of 16
    ("Paraguay", "France", 0, 1), ("Canada", "Morocco", 0, 3), ("Portugal", "Spain", 0, 1),
    ("United States", "Belgium", 1, 4), ("Brazil", "Norway", 1, 2), ("Mexico", "England", 2, 3),
    ("Argentina", "Egypt", 3, 2), ("Switzerland", "Colombia", 0, 0),
    # Quarter-finals
    ("France", "Morocco", 2, 0), ("Spain", "Belgium", 2, 1),
    ("Norway", "England", 1, 2), ("Argentina", "Switzerland", 3, 1),
    # Semi-finals
    ("Spain", "France", 2, 0), ("Argentina", "England", 2, 1),
]

ALL_RESULTS = GROUP_RESULTS + KNOCKOUT_RESULTS   # 72 group + 30 knockout = 102 tournament games

# Non-tournament games (friendlies) we still settle open positions for, but that are NOT
# counted as World Cup results and are not part of the ratings backfill.
EXTRA_RESULTS = [
    ("Vietnam", "Myanmar", 4, 0),
]


def txline_finished(competition_id: int = 72) -> list[tuple]:
    """Pull every finished fixture for a competition (default World Cup) and return
    (p1, p2, g1, g2) in kickoff order. Tries the full schedule, not just the live window,
    and reads each result from the snapshot or the 2-week historical scores."""
    from feed import TxLineClient
    client = TxLineClient()
    try:
        fixtures = client.fixtures(competition_id)     # full competition schedule
    except Exception:
        fixtures = client.fixtures()
    print(f"  TxLINE returned {len(fixtures)} fixtures; scanning for finished games...")
    out, ended = [], 0
    for f in sorted(fixtures, key=lambda x: x.get("start") or 0):
        scores = None
        for getter in (client.scores_snapshot, client.scores_historical):
            try:
                s = getter(f["fixture"])
                if s:
                    scores = s
                    break
            except Exception:
                continue
        if scores and scores[-1].ended:
            sc = scores[-1]
            out.append((f["p1"], f["p2"], sc.home, sc.away))
            ended += 1
    print(f"  found {ended} finished games with results")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--txline", action="store_true", help="also learn from TxLINE finished games")
    ap.add_argument("--k", type=float, default=K_WORLD_CUP, help="Elo K-factor (World Cup=60)")
    args = ap.parse_args()

    # reset to pristine base and clear the ledger so learning is deterministic and the
    # game count reflects exactly this tournament
    for k, v in BASE_ELO.items():
        ELO[k] = v
    reset_ledger()

    results = list(ALL_RESULTS)
    if args.txline:
        have = set(results)
        for game in txline_finished():
            if game not in have:
                results.append(game); have.add(game)

    report = learn_from_results(results, k=args.k, neutral=True, persist=True)
    print(f"learned from {len(results)} games "
          f"({len(ALL_RESULTS)} recorded + {len(results) - len(ALL_RESULTS)} live). "
          f"biggest rating moves:")
    for team, (old, new, chg) in sorted(report.items(), key=lambda kv: -abs(kv[1][2]))[:12]:
        print(f"  {team:<16} {old} -> {new}  ({chg:+d})")
    print(f"persisted to elo_overrides.json | ratings ledger now covers "
          f"{games_learned_count()} unique games (shown on the dashboard)")


if __name__ == "__main__":
    main()
