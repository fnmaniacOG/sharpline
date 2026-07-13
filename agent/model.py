"""
model.py - fair-value 1x2 probabilities for a fixture (Elo + Poisson).

Same model as the World Cup simulator: World-Football-Elo ratings (eloratings.net scale),
converted to expected goals by s = (eloHome - eloAway) / C with BASE total goals, then the
independent-Poisson scoreline grid summed into P(home)/P(draw)/P(away).

Difference from the simulator: it Monte-Carlo samples scorelines; here we sum the Poisson
grid analytically, which is exact and deterministic - better for pricing. Same expectation.

Ratings are the simulator's values; edit them there and mirror here, or tune from results.
"""

from __future__ import annotations
import json
import os
from math import exp, factorial, log10

HOME, DRAW, AWAY = "HOME", "DRAW", "AWAY"

# conversion constants (identical to the simulator)
C = 420.0          # Elo points per unit of goal supremacy
BASE_TOTAL = 2.6   # average total goals
GOAL_FLOOR = 0.2   # minimum expected goals for a side
MAX_GOALS = 10     # truncate the Poisson grid here

# World-Football-Elo ratings (mirrored from wc-simulator.html team table)
ELO = {
    "France": 2050, "Spain": 2040, "England": 1980, "Argentina": 2020, "Portugal": 1970,
    "Brazil": 1990, "Germany": 1930, "Netherlands": 1900, "United States": 1770, "Norway": 1800,
    "Morocco": 1860, "Belgium": 1830, "Colombia": 1840, "Mexico": 1720, "Japan": 1820,
    "Switzerland": 1800, "Croatia": 1820, "Uruguay": 1880, "Senegal": 1810, "Canada": 1720,
    "Côte d'Ivoire": 1740, "Ecuador": 1780, "Sweden": 1770, "Türkiye": 1780, "Australia": 1700,
    "Austria": 1790, "Czechia": 1760, "Egypt": 1700, "Ghana": 1700, "Korea Republic": 1740,
    "Scotland": 1740, "Algeria": 1730, "Congo DR": 1680, "Iraq": 1600, "Bosnia-Herzegovina": 1720,
    "Cabo Verde": 1620, "Curaçao": 1560, "Haiti": 1560, "Iran": 1780, "Jordan": 1620,
    "Saudi Arabia": 1650, "New Zealand": 1560, "Panama": 1640, "Paraguay": 1720, "Qatar": 1650,
    "South Africa": 1660, "Tunisia": 1690, "Uzbekistan": 1640,
    # a few non-WC sides for friendlies the feed may list
    "Vietnam": 1190, "Myanmar": 1085, "Thailand": 1240, "Indonesia": 1150,
}

# map common / TxLINE name variants onto the ELO keys
ALIASES = {
    "Ivory Coast": "Côte d'Ivoire", "Cote d'Ivoire": "Côte d'Ivoire",
    "USA": "United States", "US": "United States",
    "South Korea": "Korea Republic", "Korea": "Korea Republic",
    "Turkey": "Türkiye", "Turkiye": "Türkiye",
    "Cape Verde": "Cabo Verde",
    "DR Congo": "Congo DR", "Democratic Republic of the Congo": "Congo DR", "Congo": "Congo DR",
    "Bosnia": "Bosnia-Herzegovina", "Bosnia and Herzegovina": "Bosnia-Herzegovina",
    "Czech Republic": "Czechia", "Curacao": "Curaçao",
}
DEFAULT_ELO = 1600


def elo(team: str) -> float:
    if not team:
        return DEFAULT_ELO
    name = team.strip()
    if name in ELO:
        return ELO[name]
    if name in ALIASES:
        return ELO[ALIASES[name]]
    return DEFAULT_ELO


def _canon(team: str) -> str:
    """Resolve a team name to its key in the ELO table."""
    name = (team or "").strip()
    return ALIASES.get(name, name)


def implied_elo_gap(market_p: dict) -> float:
    """Back out the home-minus-away Elo gap the market implies, from the DECISIVE split.
    Ignores the draw on purpose: the draw carries totals/style info, not relative strength.
    """
    h, a = market_p.get(HOME, 0.0), market_p.get(AWAY, 0.0)
    denom = h + a
    if denom <= 0:
        return 0.0
    share = min(max(h / denom, 1e-6), 1 - 1e-6)          # home share of decisive outcomes
    return 400.0 * log10(share / (1 - share))            # inverse Elo win-expectancy


def tune_from_market(home: str, away: str, market_p: dict,
                     learn: float = 0.5, persist: bool = False) -> dict:
    """Nudge two teams' Elo toward the market-implied gap, keeping their average fixed.

    learn in [0,1] is how far to move (0.5 = halfway). This is deliberately partial: the
    market is one noisy observation, so we update toward it, we do not copy it. Writes the
    new ratings into ELO, optionally persists them, and returns a before/after report.
    """
    kh, ka = _canon(home), _canon(away)
    old_h, old_a = elo(home), elo(away)
    cur_gap = old_h - old_a
    target_gap = implied_elo_gap(market_p)
    new_gap = cur_gap + max(0.0, min(1.0, learn)) * (target_gap - cur_gap)
    mid = (old_h + old_a) / 2.0
    ELO[kh] = round(mid + new_gap / 2.0)
    ELO[ka] = round(mid - new_gap / 2.0)
    if persist:
        save_overrides([kh, ka])
    return {"cur_gap": round(cur_gap, 1), "market_gap": round(target_gap, 1),
            "applied_gap": round(new_gap, 1),
            home: (old_h, ELO[kh]), away: (old_a, ELO[ka])}


# ---- learned-ratings persistence (auto-calibration accumulates across runs) ----
OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "elo_overrides.json")


def load_overrides() -> None:
    """Merge any learned rating overrides on top of the base ELO table."""
    try:
        with open(OVERRIDES_PATH) as f:
            for k, v in json.load(f).items():
                ELO[k] = v
    except (FileNotFoundError, ValueError, OSError):
        pass


def save_overrides(team_keys: list[str]) -> None:
    """Persist the current ELO of the given (canonical) team keys, merged with prior learning."""
    existing = {}
    try:
        with open(OVERRIDES_PATH) as f:
            existing = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        pass
    for k in team_keys:
        existing[k] = ELO[k]
    try:
        with open(OVERRIDES_PATH, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


BASE_ELO = dict(ELO)   # pristine ratings before learned overrides (for deterministic re-learning)
load_overrides()


def _poisson(k: int, lam: float) -> float:
    return exp(-lam) * lam ** k / factorial(k)


def expected_goals(home_team: str, away_team: str, neutral: bool = True) -> tuple[float, float]:
    """Expected goals from the Elo gap (no home advantage; the World Cup is neutral)."""
    s = (elo(home_team) - elo(away_team)) / C
    lam_home = max(GOAL_FLOOR, (BASE_TOTAL + s) / 2.0)
    lam_away = max(GOAL_FLOOR, (BASE_TOTAL - s) / 2.0)
    return lam_home, lam_away


def one_x_two(lam_home: float, lam_away: float, max_goals: int = MAX_GOALS) -> dict:
    """Sum the independent-Poisson scoreline grid into 1x2 probabilities."""
    ph = [_poisson(i, lam_home) for i in range(max_goals + 1)]
    pa = [_poisson(j, lam_away) for j in range(max_goals + 1)]
    p_home = p_draw = p_away = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = ph[i] * pa[j]
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    total = p_home + p_draw + p_away
    return {HOME: p_home / total, DRAW: p_draw / total, AWAY: p_away / total}


def model_probs(home_team: str, away_team: str, neutral: bool = True) -> dict:
    """Fair-value 1x2 probabilities, keyed HOME (=Participant1) / DRAW / AWAY (=Participant2)."""
    lam_home, lam_away = expected_goals(home_team, away_team, neutral)
    return one_x_two(lam_home, lam_away)


def dnb_probs(home_team: str, away_team: str, neutral: bool = True) -> dict:
    """Draw-no-bet probabilities: the 1x2 model conditioned on a decisive result
    (the draw's probability is removed and its mass split by relative strength)."""
    p = model_probs(home_team, away_team, neutral)
    ph, pa = p[HOME], p[AWAY]
    total = ph + pa or 1.0
    return {HOME: ph / total, AWAY: pa / total}


def anchor_to_market(model_p: dict, market_p: dict, w_model: float = 0.30) -> dict:
    """Blend the Elo model with the de-vigged market consensus (the sharpest single source).

    A pure Poisson model overrates underdogs versus a sharp line, so we treat the market as
    a strong prior and let the model only tilt it by w_model. Returns normalized probabilities.
    Edge then becomes w_model * (model - market): the agent acts only on real conviction.
    """
    w = max(0.0, min(1.0, w_model))
    blended = {k: w * model_p.get(k, 0.0) + (1 - w) * market_p.get(k, 0.0) for k in model_p}
    total = sum(blended.values()) or 1.0
    return {k: v / total for k, v in blended.items()}


def devig(decimal_odds: dict) -> dict:
    """De-vig a decimal-odds dict into normalized implied probabilities."""
    implied = {k: 1.0 / v for k, v in decimal_odds.items() if v}
    total = sum(implied.values()) or 1.0
    return {k: v / total for k, v in implied.items()}


if __name__ == "__main__":
    p = model_probs("France", "Myanmar")
    assert abs(sum(p.values()) - 1.0) < 1e-9, p
    assert p[HOME] > p[AWAY] and p[HOME] > p[DRAW], p

    even = model_probs("Brazil", "Brazil")
    assert abs(even[HOME] - even[AWAY]) < 1e-9, even

    # alias resolution: variants must map to the same rating as the canonical name
    assert elo("Ivory Coast") == elo("Côte d'Ivoire")
    assert elo("USA") == elo("United States") and elo("South Korea") == elo("Korea Republic")

    for h, a in [("Côte d'Ivoire", "Norway"), ("Netherlands", "Morocco"), ("France", "Senegal")]:
        pr = model_probs(h, a)
        print(f"{h} v {a}:", {k: round(v, 3) for k, v in pr.items()})
    print("model checks pass")
