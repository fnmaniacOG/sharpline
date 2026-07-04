"""
discipline.py - turn a signal into a sized decision, or a pass.

This is the trade-discipline layer applied to prediction-market bets: only act on a real
edge, size by risk (half-Kelly, capped), and never bet without one. It is the difference
between an agent and an alert bot, and it is the moat.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Params:
    min_edge: float = 0.03      # need at least 3 points of de-vigged edge to act
    min_conf: float = 0.50      # and at least this much confidence
    kelly_fraction: float = 0.5  # half-Kelly: conservative because the model is an estimate
    max_stake_pct: float = 0.02  # never risk more than 2% of bankroll on one market
    min_odds: float = 1.10       # skip near-certain favorites; no payout to justify it
    max_odds: float = 7.00       # skip extreme longshots; model error dominates the price there


def decide(signal: dict, priced: dict, bankroll: float, p: Params = Params()) -> dict:
    """signal: from signals.make_signal. priced: the per-outcome dict from pricing.price_market."""
    d = priced["decimal"]
    prob = priced["modelP"]
    b = d - 1.0

    def passed(reason):
        return {"action": "PASS", "outcome": signal["outcome"], "reason": reason}

    if signal["direction"] != "BACK":
        return passed("no value edge (model not above market)")
    if signal["edge"] < p.min_edge:
        return passed(f"edge {signal['edge']} below {p.min_edge} floor")
    if signal["confidence"] < p.min_conf:
        return passed(f"confidence {signal['confidence']} below {p.min_conf} floor")
    if d < p.min_odds or b <= 0:
        return passed("odds too short to justify the risk")
    if d > p.max_odds:
        return passed(f"odds {d} above {p.max_odds} ceiling; longshot model error")

    # half-Kelly sizing, capped
    kelly = (b * prob - (1 - prob)) / b
    frac = max(0.0, kelly) * p.kelly_fraction
    frac = min(frac, p.max_stake_pct)
    stake = round(bankroll * frac, 2)
    if stake <= 0:
        return passed("kelly says zero size")

    return {
        "action": "BET",
        "outcome": signal["outcome"],
        "side": "BACK",
        "decimal": d,
        "modelP": prob,
        "stake": stake,
        "fractionOfBankroll": round(frac, 4),
        "edge": signal["edge"],
        "confidence": signal["confidence"],
        "reason": signal["rationale"],
    }


if __name__ == "__main__":
    # a real edge + decent odds -> a sized bet
    priced = {"decimal": 4.5, "modelP": 0.27, "marketTrueP": 0.22, "edge": 0.05, "ev": 0.215}
    sig = {"outcome": "DRAW", "direction": "BACK", "edge": 0.05, "ev": 0.215,
           "confidence": 0.7, "rationale": "model says underpriced"}
    d = decide(sig, priced, bankroll=1000)
    print(d)
    assert d["action"] == "BET" and 0 < d["stake"] <= 20  # <= 2% of 1000

    # no edge -> pass
    sig2 = {**sig, "edge": 0.01, "direction": "BACK", "confidence": 0.2}
    priced2 = {**priced, "edge": 0.01}
    assert decide(sig2, priced2, 1000)["action"] == "PASS"
    print("discipline checks pass")
