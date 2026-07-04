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
from ratings import learn_from_results, K_WORLD_CUP

# Real group-stage results (neutral venues). (home, away, goals_home, goals_away).
GROUP_RESULTS = [
    ("Scotland", "Haiti", 1, 0),
    ("Morocco", "Brazil", 1, 1),
    ("United States", "Paraguay", 4, 1),
    ("Australia", "Türkiye", 2, 0),
    ("Germany", "Curaçao", 7, 1),
    ("Côte d'Ivoire", "Ecuador", 1, 0),
    ("Sweden", "Tunisia", 5, 1),
    ("Japan", "Netherlands", 2, 2),
    ("Iran", "New Zealand", 2, 2),
    ("Belgium", "Egypt", 1, 1),
    ("Uruguay", "Saudi Arabia", 1, 1),
    ("Spain", "Cabo Verde", 0, 0),
    ("France", "Senegal", 3, 1),
    ("Norway", "Iraq", 4, 1),
    ("Argentina", "Algeria", 3, 0),
    ("Austria", "Jordan", 3, 1),
    ("Colombia", "Uzbekistan", 3, 1),
    ("Congo DR", "Portugal", 1, 1),
    ("England", "Croatia", 4, 2),
    ("Ghana", "Panama", 1, 0),
]


def txline_finished() -> list[tuple]:
    """Pull finished fixtures from TxLINE and return (p1, p2, g1, g2) in kickoff order."""
    from feed import TxLineClient
    client = TxLineClient()
    fixtures = client.fixtures()
    out = []
    for f in sorted(fixtures, key=lambda x: x.get("start") or 0):
        try:
            scores = client.scores_snapshot(f["fixture"])
        except Exception:
            continue
        if scores and scores[-1].ended:
            s = scores[-1]
            out.append((f["p1"], f["p2"], s.home, s.away))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--txline", action="store_true", help="also learn from TxLINE finished games")
    ap.add_argument("--k", type=float, default=K_WORLD_CUP, help="Elo K-factor (World Cup=60)")
    args = ap.parse_args()

    # reset to pristine base so learning is deterministic
    for k, v in BASE_ELO.items():
        ELO[k] = v

    results = list(GROUP_RESULTS)
    if args.txline:
        seen = {tuple(sorted((h, a))) for h, a, _, _ in results}
        for h, a, gh, ga in txline_finished():
            if tuple(sorted((h, a))) not in seen:
                results.append((h, a, gh, ga))
        print(f"finished games: {len(GROUP_RESULTS)} seed + "
              f"{len(results) - len(GROUP_RESULTS)} from TxLINE")

    report = learn_from_results(results, k=args.k, neutral=True, persist=True)
    print(f"learned from {len(results)} games. biggest rating moves:")
    for team, (old, new, chg) in sorted(report.items(), key=lambda kv: -abs(kv[1][2]))[:12]:
        print(f"  {team:<16} {old} -> {new}  ({chg:+d})")
    print("persisted to elo_overrides.json")


if __name__ == "__main__":
    main()
