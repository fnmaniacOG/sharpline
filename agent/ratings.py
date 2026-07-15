"""
ratings.py - learn team Elo from actual results (the canonical World-Football-Elo update).

After each game, both teams' ratings move by how far the result beat expectation, scaled by
goal difference and a tournament weight K. This is how Elo is meant to update, and unlike the
market-anchor tune it is NOT circular: it learns from what actually happened on the pitch.

Feed it finished games (group stage first, then completed knockouts) in chronological order
and it converges the ratings toward current form, then persists them via model.save_overrides.
"""

from __future__ import annotations
import json
import os

from model import ELO, elo, _canon, save_overrides, DEFAULT_ELO

# a persistent ledger of every game the ratings have learned from (shared by backfill + live)
LEDGER_PATH = os.path.join(os.path.dirname(__file__), "..", "dashboard", "learned_games.json")


def _load_ledger() -> set:
    try:
        with open(LEDGER_PATH) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def game_key(home, away, gh, ga) -> str:
    return f"{home}|{away}|{gh}-{ga}"


def record_games(keys) -> int:
    """Add game keys to the learned-games ledger (deduped); return the running total."""
    led = _load_ledger()
    led.update(keys)
    try:
        os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
        with open(LEDGER_PATH, "w") as f:
            json.dump(sorted(led), f)
    except OSError:
        pass
    return len(led)


def games_learned_count() -> int:
    return len(_load_ledger())


def reset_ledger() -> None:
    try:
        os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
        with open(LEDGER_PATH, "w") as f:
            json.dump([], f)          # overwrite (works even where delete is blocked)
    except OSError:
        pass

# World Cup finals weight (World-Football-Elo uses 60 for World Cup matches)
K_WORLD_CUP = 60.0
HOME_ADV = 100.0   # applied only for non-neutral games


def expected_score(elo_a: float, elo_b: float, neutral: bool = True) -> float:
    """Expected result for A (1=win .5=draw 0=loss), the Elo win-expectancy."""
    h = 0.0 if neutral else HOME_ADV
    return 1.0 / (1.0 + 10 ** (-(elo_a - elo_b + h) / 400.0))


def _gd_multiplier(goal_diff: int) -> float:
    """World-Football-Elo goal-difference weighting."""
    gd = abs(goal_diff)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11.0 + gd) / 8.0


def elo_update(elo_a: float, elo_b: float, goals_a: int, goals_b: int,
               k: float = K_WORLD_CUP, neutral: bool = True) -> tuple[float, float]:
    """Return the two teams' new ratings after one game (symmetric, zero-sum)."""
    we_a = expected_score(elo_a, elo_b, neutral)
    w_a = 1.0 if goals_a > goals_b else (0.0 if goals_a < goals_b else 0.5)
    delta = k * _gd_multiplier(goals_a - goals_b) * (w_a - we_a)
    return elo_a + delta, elo_b - delta


def learn_from_results(results: list[tuple], k: float = K_WORLD_CUP,
                       neutral: bool = True, persist: bool = True) -> dict:
    """Apply Elo updates for a list of (home, away, goals_home, goals_away) in order.

    Returns a report of how far each team moved. Persists learned ratings if persist=True.
    """
    start = {}
    touched: set[str] = set()
    for home, away, gh, ga in results:
        kh, ka = _canon(home), _canon(away)
        for key in (kh, ka):
            if key not in start:
                start[key] = ELO.get(key, DEFAULT_ELO)
        new_h, new_a = elo_update(elo(home), elo(away), int(gh), int(ga), k, neutral)
        ELO[kh] = round(new_h)
        ELO[ka] = round(new_a)
        touched.update((kh, ka))
    if persist and touched:
        save_overrides(sorted(touched))
        record_games([game_key(h, a, gh, ga) for (h, a, gh, ga) in results])
    return {t: (start[t], ELO[t], ELO[t] - start[t]) for t in sorted(touched)}


if __name__ == "__main__":
    # two even teams; a 3-0 win should raise the winner and drop the loser, zero-sum
    a, b = elo_update(1700, 1700, 3, 0, k=60, neutral=True)
    assert a > 1700 > b and abs((a - 1700) + (b - 1700)) < 1e-9, (a, b)
    # bigger blowout moves ratings more than a 1-0
    a1, _ = elo_update(1700, 1700, 1, 0, k=60)
    a3, _ = elo_update(1700, 1700, 3, 0, k=60)
    assert (a3 - 1700) > (a1 - 1700)
    # beating a much stronger side moves you more than beating a weaker one
    up_vs_strong, _ = elo_update(1600, 2000, 1, 0, k=60)
    up_vs_weak, _ = elo_update(1600, 1200, 1, 0, k=60)
    assert (up_vs_strong - 1600) > (up_vs_weak - 1600)
    print("elo update:", round(a, 1), round(b, 1))
    print("ratings result-learning checks pass")
