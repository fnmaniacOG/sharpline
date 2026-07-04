"""
pricing.py - fair value vs market.

Two jobs:
  1. de-vig the TxLINE consensus odds into a true market probability (the "sum to 100" rule:
     raw implied probabilities sum to more than 100%; normalize them).
  2. compare that to the model's fair-value probability (from the Monte Carlo WC simulator)
     to get the edge and the expected value of a bet.

Works in decimal odds throughout (decimal = 1 / implied_probability). Pure, no network.
"""

from __future__ import annotations


def implied_prob(decimal_odds: float) -> float:
    return 1.0 / decimal_odds if decimal_odds > 0 else 0.0


def devig(market: dict[str, float]) -> dict[str, float]:
    """market: {outcome: decimal_odds} for the mutually exclusive outcomes of one market line.
    Returns the vig-free true probability per outcome (they sum to 1.0)."""
    raw = {k: implied_prob(v) for k, v in market.items()}
    overround = sum(raw.values())
    if overround <= 0:
        return {k: 0.0 for k in market}
    return {k: p / overround for k, p in raw.items()}


def overround(market: dict[str, float]) -> float:
    """How much vig is in the line. 1.00 = none; 1.05 = 5% book margin."""
    return sum(implied_prob(v) for v in market.values())


def edge(model_p: float, market_true_p: float) -> float:
    """Model probability minus the vig-free market probability. Positive = model says underpriced."""
    return model_p - market_true_p


def expected_value(model_p: float, decimal_odds: float) -> float:
    """EV per 1 unit staked at these odds, using the model's probability. >0 = +EV bet."""
    return model_p * decimal_odds - 1.0


def price_market(model_probs: dict[str, float], market_odds: dict[str, float]) -> dict[str, dict]:
    """One line: combine model + de-vigged market into per-outcome edge and EV."""
    true_p = devig(market_odds)
    out = {}
    for outcome, odds in market_odds.items():
        mp = model_probs.get(outcome, 0.0)
        out[outcome] = {
            "decimal": odds,
            "marketTrueP": round(true_p.get(outcome, 0.0), 4),
            "modelP": round(mp, 4),
            "edge": round(edge(mp, true_p.get(outcome, 0.0)), 4),
            "ev": round(expected_value(mp, odds), 4),
        }
    return out


if __name__ == "__main__":
    # 3-way line: France / Draw / Senegal, with vig
    odds = {"FRA": 1.52, "DRAW": 4.5, "SEN": 7.7}
    print("overround:", round(overround(odds), 4))          # > 1.0
    tp = devig(odds)
    print("true probs:", {k: round(v, 3) for k, v in tp.items()}, "sum:", round(sum(tp.values()), 3))
    # model thinks the draw is more likely than the market does -> positive edge on DRAW
    model = {"FRA": 0.64, "DRAW": 0.25, "SEN": 0.11}
    priced = price_market(model, odds)
    print("DRAW edge:", priced["DRAW"]["edge"], "EV:", priced["DRAW"]["ev"])
    assert abs(sum(tp.values()) - 1.0) < 1e-9, "de-vigged probs must sum to 1"
    assert priced["DRAW"]["edge"] > 0, "model > market on draw should be +edge"
    print("pricing checks pass")
